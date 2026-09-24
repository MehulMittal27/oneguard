"""English rule-based fallback compiler (rules.md §10, T7: English only).

Used when the LLM is unavailable, times out, or its reading fails lint. It reads only
what it recognises and never guesses a number: a restriction it cannot map becomes an
``unverifiable`` rule or an open question (T2), and every boundary word is kept (T3).
The instruction is the customer's own text; nothing here reads shop text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from oneguard.compiler.draft import (
    COUNTRY_NAMES,
    KNOWN_SHOP_FIELD,
    MONEY_FIELDS,
    WEEKDAYS,
    ParsedDraft,
    RuleSpec,
    finalize,
    fmt_amount,
    next_weekday,
    number,
    to_chf,
)

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30,
}
_NUM = r"(?:\d+|" + "|".join(NUMBER_WORDS) + r")"

_CURRENCY = {"chf": "CHF", "fr": "CHF", "fr.": "CHF", "francs": "CHF", "franc": "CHF",
             "eur": "EUR", "€": "EUR", "euro": "EUR", "euros": "EUR",
             "gbp": "GBP", "£": "GBP", "pounds": "GBP", "usd": "USD", "$": "USD", "dollars": "USD"}
_AMOUNT = re.compile(
    r"(?P<cur>\bCHF|\bEUR|\bGBP|\bUSD|\bFr\.?|€|£|\$)\s?(?P<num>\d[\d,']*(?:\.\d+)?)"
    r"|(?P<num2>\d[\d,']*(?:\.\d+)?)\s?(?P<cur2>CHF\b|EUR\b|GBP\b|USD\b|francs?\b|euros?\b|pounds\b|dollars\b)",
    re.IGNORECASE,
)
# "max CHF 120 per order and 300 a week": a bare number joined to an amount by "and" /
# "or" and followed by its own scope ("a week", "per order") is an amount in the same
# currency. ``with_shared_currency`` writes the currency in, for the parser and lint alike.
_SHARED_CURRENCY = re.compile(
    r"(?:(?P<cur>\bCHF|\bEUR|\bGBP|\bUSD|\bFr\.?|€|£|\$)\s?\d[\d,']*(?:\.\d+)?"
    r"|\d[\d,']*(?:\.\d+)?\s?(?P<cur2>CHF\b|EUR\b|GBP\b|USD\b|francs?\b|euros?\b|pounds\b|dollars\b))"
    r"[^.;!?\d]*?(?:,\s*|\s(?:and|or)\s+)"
    r"(?:(?:up to|max(?:imum)?|at most|under|below|less than|no more than)\s+)?"
    r"(?P<num>\d[\d,']*(?:\.\d+)?)"
    r"(?=\s+(?:a|per|each|every|in|over|within|across)\s+(?:any\s+|a\s+|the\s+)?(?:rolling\s+)?"
    r"(?:\d+[\s-]+days?|day|week|month|fortnight|order|purchase)\b)",
    re.IGNORECASE,
)
_INCLUSIVE_BEFORE = re.compile(
    r"((?:never|not|don't|do not) (?:spend|pay|go) (?:more|over) than|"
    r"at or below|at or under|at most|no more than|not more than|no higher than|max(?:imum)?\.?|"
    r"up to|not exceeding|not over|within|limit(?: of)?|budget(?: of)?|cap(?: of)?)\s*$", re.IGNORECASE)
_STRICT_BEFORE = re.compile(r"(under|less than|below|lower than|cheaper than)\s*$", re.IGNORECASE)
_INCLUSIVE_AFTER = re.compile(r"^\s*(or less|or under|or below|or lower|max(?:imum)?|at most|tops)\b", re.IGNORECASE)
_EXACT_BEFORE = re.compile(r"(exactly|for exactly)\s*$", re.IGNORECASE)

_PERIOD = re.compile(
    rf"\b(?:across|over|in|within|per|each|every|for)\s+(?:any\s+|a\s+|the\s+)?(?:rolling\s+)?"
    rf"(?P<n>{_NUM})(?:\s+|-)days?\b(?:[\s-]+(?:window|period))?"
    rf"|\b(?P<word>per week|a week|each week|every week|weekly|per month|"
    rf"a month|each month|every month|monthly|per fortnight|a fortnight)\b",
    re.IGNORECASE,
)
_PER_ITEM_AFTER = re.compile(
    r"^\W*(each|apiece|a piece|per (?:item|ticket|piece|unit|one|night)|a night|each night)\b(?!\s+order)",
    re.IGNORECASE)
_PER_ITEM_BEFORE = re.compile(r"\beach (?:item|ticket|piece|one|night)\b|\bper (?:item|ticket|piece|unit|night)\b",
                              re.IGNORECASE)
# "purchases up to CHF 300 each", "electronics up to CHF 300 each": "each" is each
# purchase, a per-order cap, unless items are counted ("two tickets, max CHF 90 each").
_EACH_ORDER = re.compile(r"\b(?:purchases?|orders?|bookings?|deliveries|payments?|baskets?)\b", re.IGNORECASE)
_COUNTED = re.compile(
    rf"\b(?<!size )(?:{'|'.join(w for w in NUMBER_WORDS if w != 'one')}|[2-9]|\d\d+)\s+"
    r"(?:[a-z-]+\s+){0,2}?(?!(?:days|nights|hours|weeks|months|years)\b)[a-z-]+s\b",
    re.IGNORECASE,
)

# Words for item types (C3). Value: (categories, source when the word is used).
ITEM_WORDS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\bgrocer(?:y|ies)\b", re.IGNORECASE), ["groceries"]),
    (re.compile(r"\b(?:clothing|clothes|apparel)\b", re.IGNORECASE), ["clothing"]),
    (re.compile(r"\b(?:lunch(?:es)?|dinners?|breakfasts?|meals?)\b", re.IGNORECASE), ["dining", "food_delivery"]),
    (re.compile(r"\bbooks?\b", re.IGNORECASE), ["books"]),
    (re.compile(r"\belectronics\b", re.IGNORECASE), ["electronics"]),
    (re.compile(r"\b(?:fuel|petrol|diesel)\b", re.IGNORECASE), ["fuel"]),
    (re.compile(r"\bcosmetics\b", re.IGNORECASE), ["cosmetics"]),
    (re.compile(r"\b(?:gym )?membership\b", re.IGNORECASE), ["membership"]),
    (re.compile(r"\bsubscriptions?\b", re.IGNORECASE), ["subscriptions"]),
    (re.compile(r"\bgift cards?\b", re.IGNORECASE), ["gift_card"]),
    (re.compile(r"\b(?:everyday )?household (?:items|supplies|goods|basics|essentials)\b", re.IGNORECASE),
     ["household"]),
    (re.compile(r"\bhotels?\b", re.IGNORECASE), ["hotel"]),
    (re.compile(r"\b(?:flights?|plane tickets?|(?:travel )?insurance)\b", re.IGNORECASE), ["travel"]),
]
# Things a customer may exclude that no item category holds (wine and spirits are
# "groceries" in the served pack): the rule is unverifiable, and an engine gap.
_NO_CATEGORY = re.compile(r"alcohol(?:ic drinks)?|wine|spirits|beer|tobacco|premium tiers?|annual prepayments?"
                          r"|(?:annual|yearly) (?:plans?|payments?)", re.IGNORECASE)
# "no new services": only the shops already used (C9).
_NO_NEW_SHOPS = re.compile(r"\bno new (?:services|shops|sellers|merchants|providers|suppliers)\b", re.IGNORECASE)
_CATEGORY_HEADS = {"item", "items", "groceries", "grocery", "clothing", "clothes", "apparel",
                   "lunch", "dinner", "breakfast", "meal", "meals", "books", "electronics",
                   "fuel", "petrol", "stuff", "things", "supplies", "cosmetics", "hotel", "hotels",
                   "shopping", "subscriptions", "flights", "flight", "dinners"}
_GENERIC_ITEMS = {"present", "gift", "thing", "product", "order", "purchase"}
_VAGUE_ITEMS = {"something", "anything", "stuff"}
_FILLER = {"worn", "old", "new", "ordinary", "usual", "favourite", "favorite", "regular",
           "replacement", "same"}

SHOP_KIND: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsports?\b|\bsporting\b|\boutdoor\b", re.IGNORECASE), "sporting_goods"),
    (re.compile(r"\bsupermarkets?\b", re.IGNORECASE), "groceries"),
    (re.compile(r"\belectronics?\b|\btech\b", re.IGNORECASE), "electronics"),
    (re.compile(r"\bbook\b", re.IGNORECASE), "books"),
    (re.compile(r"\b(?:clothing|clothes|fashion)\b", re.IGNORECASE), "clothing"),
    (re.compile(r"\bgrocery\b", re.IGNORECASE), "groceries"),
    (re.compile(r"\b(?:sustainable|eco)\b", re.IGNORECASE), "sustainable_goods"),
    (re.compile(r"\bpet\b", re.IGNORECASE), "pet_care"),
    (re.compile(r"\b(?:hardware|diy)\b", re.IGNORECASE), "home_improvement"),
    (re.compile(r"\b(?:photo|camera)\b", re.IGNORECASE), "photography"),
    (re.compile(r"\b(?:health|pharmacy)\b", re.IGNORECASE), "health"),
]
COUNTRY_WORDS: dict[str, str] = {
    "swiss": "CH", "switzerland": "CH", "german": "DE", "germany": "DE", "french": "FR",
    "france": "FR", "italian": "IT", "italy": "IT", "austrian": "AT", "austria": "AT",
    "dutch": "NL", "netherlands": "NL", "british": "GB", "uk": "GB", "american": "US",
    "us": "US", "usa": "US",
}
_DAY_NAMES = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
              "friday": "fri", "saturday": "sat", "sunday": "sun"}

_SHOP_NOUNS = r"shops?|sellers?|stores?|merchants?|places?|retailers?|supermarkets?|services|providers|restaurants"
_KNOWN_SHOP = re.compile(
    rf"\b(?:{_SHOP_NOUNS})\s+(?:that\s+)?(?:I|we)\s+"
    r"(?:have\s+|'ve\s+|already\s+)*(?:use|used|bought from|shopped at|ordered from|know|trust)\b"
    rf"|\b(?:known|familiar) (?:{_SHOP_NOUNS})\b|\bmy (?:usual|regular) (?:{_SHOP_NOUNS})\b"
    r"|\bmy (?:current|existing) subscriptions\b",
    re.IGNORECASE,
)
_SHOP_PHRASE = re.compile(
    r"\b(?:from|at)\s+(?:a|an|the)?\s*(?P<kind>(?:[\w-]+\s+){0,2}?[\w-]+)\s+"
    r"(?P<noun>retailers?|shops?|stores?|sellers?|merchants?|vendors?|supermarkets?)\b",
    re.IGNORECASE,
)
_ITEM_VERB = re.compile(
    r"(?<!per )(?<!each )(?<!an )(?<!the )(?<!a )(?<!one )(?<!two )"  # "per order", "one order": a noun
    r"\b(?:buy|order|replace|renew|get|purchase|book|need|want|top up)"
    r"(?!\s+(?:a|per|each|every)\s+(?:day|week|month|night)\b)"  # "one lunch order a day": a noun
    r"\s+(?:me\s+|us\s+|for me\s+)?"
    r"(?P<det>(?:(?:the|my|our|a|an|some|one|two|three|four|five|six|\d+)\s+)*)"
    r"(?P<phrase>[\w'-]+(?:\s+[\w'-]+){0,4}?)"
    r"(?=\s+(?:I|we)\s+(?:chose|picked|selected|want|like|need)|\s+in size|\s+size\b|\s+from\b|\s+for\b|"
    r"\s+on\b|\s+under\b|\s+up to\b|\s+max\b|\s+at\b|\s+with\b|\s+only\b|\s+each\b|\s+costing\b|"
    r"\s+that\b|\s+which\b|\s+must\b|\s+if\b|\s+and\b|\s+in\b|\s+online\b|\s*[,.;:]|\s*$)",
    re.IGNORECASE,
)
_PICKED = re.compile(r"^\s+(?:I|we)\s+(?:chose|picked|selected)\b", re.IGNORECASE)


@dataclass
class _Reading:
    specs: list[RuleSpec] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    requested_item: str | None = None
    nothing_extra: bool = False
    uncertainty: str = "ask"
    count: int | None = None
    item_noun: str | None = None
    nights: int | None = None


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[.;!?](?:\s+|$)|,\s+|\s+and\s+|\s+but\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


def with_shared_currency(text: str) -> str:
    """The instruction with the currency written in front of each bare follow-on amount
    ("max CHF 120 per order and 300 a week" -> "... and CHF 300 a week")."""
    for _ in range(8):  # "CHF 50 per order, 200 a week and 500 a month": one per pass
        m = _SHARED_CURRENCY.search(text)
        if not m:
            break
        key = (m.group("cur") or m.group("cur2")).lower()
        cur = _CURRENCY.get(key) or _CURRENCY.get(key.rstrip("s")) or "CHF"
        text = f"{text[: m.start('num')]}{cur} {text[m.start('num'):]}"
    return text


def _counted(money: str) -> bool:
    """The instruction counts its items ("two tickets"): "X each" is then per item."""
    return bool(_COUNTED.search(_AMOUNT.sub(" ", money)))


def each_is_per_purchase(instruction: str, words: str) -> bool:
    """"up to CHF 300 each" with no counted items: each purchase, a per-order cap. The
    LLM path reads a per-item limit through this, so both readings agree."""
    m = _AMOUNT.search(words)
    if not m:
        return False
    per_item = _PER_ITEM_AFTER.search(words[m.end():]) or _PER_ITEM_BEFORE.search(words[: m.start()])
    each = bool(per_item) and per_item.group(0).strip(" ,;:-").lower() == "each"
    return each and not _counted(with_shared_currency(" ".join(instruction.split())))


def _num(word: str) -> int | None:
    word = word.lower()
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word)


# --- Money (C1, C2, C12 per-item) ----------------------------------------------------
def _amounts(reading: _Reading, clause: str, counted: bool = False) -> None:
    """Each amount is read in its own stretch of the clause, from the end of the amount
    before it to the start of the one after it: "never spend more than CHF 100 per order
    or CHF 250 in any 7-day window" is a per-order cap and a 7-day cap. An amount with no
    boundary word of its own shares the one before it ("more than X or Y"). ``counted``:
    the instruction counts its items ("two tickets"), so "X each" is per item."""
    found = list(_AMOUNT.finditer(clause))
    shared: tuple[str, str] | None = None
    for i, m in enumerate(found):
        raw = (m.group("num") or m.group("num2")).replace(",", "").replace("'", "")
        key = (m.group("cur") or m.group("cur2") or "").lower()
        cur = _CURRENCY.get(key) or _CURRENCY.get(key.rstrip("s")) or "CHF"
        start = found[i - 1].end() if i else 0
        end = found[i + 1].start() if i + 1 < len(found) else len(clause)
        before, after = clause[start: m.start()], clause[m.end(): end]
        if re.search(r"same price", clause, re.IGNORECASE):
            continue
        if _EXACT_BEFORE.search(before):
            op, source = "=", "exact"
        elif _INCLUSIVE_AFTER.search(after) or _INCLUSIVE_BEFORE.search(before):
            op, source = "<=", "exact"
        elif _STRICT_BEFORE.search(before):
            op, source = "<", "exact"
        elif shared and re.fullmatch(r"[\s\w]*\b(?:or|and)\s*", before, re.IGNORECASE):
            op, source = shared
        else:
            op, source = "<=", "inferred"  # "spend CHF 100", "for CHF 50": a cap, read inclusively
        shared = (op, source)
        value = number(Decimal(raw))
        local = f"{before} {clause[m.start(): m.end()]} {after}"
        period = _PERIOD.search(after) or (_PERIOD.search(before) if i == 0 else None)
        per_item = _PER_ITEM_AFTER.search(after) or _PER_ITEM_BEFORE.search(before)
        if per_item and per_item.group(0).strip(" ,;:-").lower() == "each" \
                and (_EACH_ORDER.search(clause[: m.start()]) or not counted):
            per_item = None  # "purchases up to CHF 300 each", "electronics up to CHF 300 each": each purchase
        if period:
            days = _period_days(period)
            reading.specs.append(RuleSpec(
                field="authorization.billing_amount_chf", operator=op, value=value, currency=cur,
                scope="period", period_days=days, words=local.strip(), source=source))
        elif per_item:
            reading.specs.append(RuleSpec(
                field="items[].unit_price_chf", operator=op, value=value, currency=cur,
                scope="purchase", words=clause, source=source))
        else:
            reading.specs.append(RuleSpec(
                field="authorization.billing_amount_chf", operator=op, value=value, currency=cur,
                scope="purchase", words=clause, source=source))


def _period_days(m: re.Match[str]) -> int:
    if m.group("n"):
        return _num(m.group("n")) or 7
    word = m.group("word").lower()
    if "fortnight" in word:
        return 14
    return 30 if "month" in word else 7


# --- Items (C3, C4, C5, C6, C10, C12 quantity) ---------------------------------------
def _item(reading: _Reading, text: str) -> None:
    m = _ITEM_VERB.search(text)
    phrase = m.group("phrase").strip() if m else ""
    det = (m.group("det") or "").lower().split() if m else []
    words = [w for w in re.findall(r"[\w'-]+", phrase.lower())]
    head = words[-1] if words else ""

    for d in det:
        n = _num(d)
        if n and n >= 2:
            reading.count = n
    categories: list[str] = []
    for pattern, cats in ITEM_WORDS:
        if pattern.search(phrase or _positive(text)):
            categories += [c for c in cats if c not in categories]
            if "household" in cats and re.search(r"groceries", phrase, re.IGNORECASE):
                categories.remove("household")

    if head in _VAGUE_ITEMS or any(w in _VAGUE_ITEMS for w in words[:1]):
        reading.questions.append("What should the agent buy?")
        return
    if head in _GENERIC_ITEMS and m and _PICKED.match(text[m.end():]):
        picked = _PICKED.match(text[m.end():])
        said = " ".join(det + [phrase]) + text[m.end(): m.end() + picked.end()]
        reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=said.strip(),
                                      words=said.strip()))
        reading.item_noun = head
        return
    if head in _CATEGORY_HEADS or (not phrase and categories):
        if categories:
            exact = any(re.search(rf"\b{c}\b", text, re.IGNORECASE) for c in categories)
            reading.specs.append(RuleSpec(
                field="items[].item_category", operator="in", value=categories,
                words=phrase or text, source="exact" if exact else "inferred"))
        return
    if not phrase:
        return
    item = " ".join(w for w in words if w not in _FILLER)
    reading.requested_item = item
    reading.item_noun = head
    if categories:  # "gym membership": the item has a type as well (oracle C3)
        reading.specs.append(RuleSpec(field="items[].item_category", operator="in", value=categories,
                                      words=phrase, source="inferred"))


_NEGATED = re.compile(r"\b(?:no|never|not|without|except|excluding)\b[^.;:!?]*", re.IGNORECASE)


def _positive(text: str) -> str:
    """The instruction without its negated stretches: item types named after "no" or
    "never" are exclusions (C4), never what the customer allows (C3)."""
    return _NEGATED.sub(" ", text)


def _blocked(reading: _Reading, text: str) -> None:
    """Each "no X" / "except X": an item type (one C4 rule for all of them), "no new
    services" (only the shops already used, C9), or a thing no category holds
    ("no alcohol", "no premium tiers": unverifiable, an engine gap). Anything else
    ("never at the weekend", "no more than CHF 50") is read elsewhere."""
    blocked: list[str] = []
    said: list[str] = []
    for m in re.finditer(r"\b(?:no|never|except|excluding|without)\s+(?:any\s+)?(?P<what>[\w -]+?)"
                         r"(?=\s*[,.;:!?]|\s+or\s|\s*$)", text, re.IGNORECASE):
        what = m.group("what").strip()
        words = m.group(0).strip()
        cats = next((c for p, c in ITEM_WORDS if p.fullmatch(what)), None)
        if cats:
            blocked += [c for c in cats if c not in blocked]
            said.append(words)
        elif _NO_NEW_SHOPS.fullmatch(words):
            if not any(s.field == KNOWN_SHOP_FIELD for s in reading.specs):
                reading.specs.append(RuleSpec(field=KNOWN_SHOP_FIELD, operator="=", value="true", words=words))
        elif _NO_CATEGORY.fullmatch(what):
            reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=words, words=words))
    if blocked:
        reading.specs.append(RuleSpec(field="items[].item_category", operator="not_in", value=blocked,
                                      words=", ".join(said)))


def _details(reading: _Reading, text: str) -> None:
    if m := re.search(r"\bsize\s+(?P<n>\d{2}(?:[.,]5)?)\b", text, re.IGNORECASE):
        n = Decimal(m.group("n").replace(",", "."))
        reading.specs.append(RuleSpec(field="items[].size_eu", operator="=", value=number(n), words=m.group(0)))
    elif m := re.search(r"\bsize\s+(?P<s>XXXL|XXL|XL|XXS|XS|S|M|L|small|medium|large)\b", text, re.IGNORECASE):
        s = {"small": "S", "medium": "M", "large": "L"}.get(m.group("s").lower(), m.group("s").upper())
        reading.specs.append(RuleSpec(field="items[].size_letter", operator="=", value=s, words=m.group(0)))
    if reading.count:
        noun = f" {reading.item_noun}" if reading.item_noun else ""
        word = next((w for w, n in NUMBER_WORDS.items() if n == reading.count), str(reading.count))
        reading.specs.append(RuleSpec(field="cart.quantity", operator="=", value=reading.count,
                                      words=word if re.search(rf"\b{word}\b", text, re.IGNORECASE) else f"{reading.count}{noun}"))
    if re.search(r"\b(?:do not|don't|never)\s+add\s+anything\b|\bnothing\s+(?:extra|else)\b|\bno\s+(?:add-?ons|extras)\b"
                 r"|\bonly what I (?:asked|ask) for\b", text, re.IGNORECASE):
        reading.nothing_extra = True


# --- Terms (C7) and delivery (C12) ---------------------------------------------------
def _terms(reading: _Reading, text: str, today: date | None) -> None:
    m = re.search(rf"\breturn(?:ed|s|able)?\b[^.;]*?(?P<n>{_NUM})[\s-]*days?(?P<more>\s+or\s+more)?"
                  rf"|(?P<n2>{_NUM})[\s-]*days?\s+returns?\b", text, re.IGNORECASE)
    if m:
        n = _num(m.group("n") or m.group("n2"))
        if n is not None:
            reading.specs.append(RuleSpec(field="order.return_window_days", operator=">=", value=n,
                                          words=m.group(0).strip()))
    elif w := re.search(r"\b(?:returnable|can be returned|with (?:free )?returns)\b", text, re.IGNORECASE):
        reading.specs.append(RuleSpec(field="order.order_returnable", operator="=", value="true", words=w.group(0)))
    # "refundable rate only" (hotels, fares): the booking can be cancelled for a refund.
    if w := re.search(r"(?<!non-)(?<!non )\b(?:cancell?able|can be cancell?ed|free cancell?ation|"
                      r"refundable (?:rate|booking|fare|ticket|room)s?)\b", text, re.IGNORECASE):
        reading.specs.append(RuleSpec(field="order.order_cancellable", operator="=", value="true", words=w.group(0)))

    days = "|".join(_DAY_NAMES)
    if m := re.search(rf"\b(?:arrive|arrives|arriving|delivered|delivery|deliver)\b[^,.;]*?\bby\s+"
                      rf"(?P<day>{days}|\d{{4}}-\d{{2}}-\d{{2}})\b", text, re.IGNORECASE):
        words = re.search(rf"[^,.;]*\bby\s+{re.escape(m.group('day'))}", text[: m.end()], re.IGNORECASE)
        said = (words.group(0) if words else m.group(0)).strip()
        day = m.group("day").lower()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            reading.specs.append(RuleSpec(field="authorization.delivery_by", operator="<=", value=day, words=said))
        elif today is not None:
            by = next_weekday(today, _DAY_NAMES[day])
            reading.specs.append(RuleSpec(field="authorization.delivery_by", operator="<=", value=by.isoformat(),
                                          words=said, note=f'"{said}", counted from {today:%d %b %Y}',
                                          value_from=f"next {day} after {today.isoformat()}"))
        else:
            reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=said, words=said))
            reading.questions.append(f'Which date do you mean by "{said}"?')


# --- Time (C12) ----------------------------------------------------------------------
def _time(reading: _Reading, text: str) -> None:
    never_weekend = re.search(r"\b(?:never|not|no)\s+(?:on|at|during)?\s*(?:the\s+)?weekends?\b", text, re.IGNORECASE)
    if m := re.search(r"\b(?:on\s+)?weekdays(?:\s+only)?\b|\bweeknights?\b|\bmonday to friday\b", text, re.IGNORECASE):
        # "Weeknight dinners only ... Never at the weekend": one rule, Mon-Fri.
        reading.specs.append(RuleSpec(field="authorization.weekday", operator="in",
                                      value=list(WEEKDAYS[:5]), words=m.group(0).strip()))
    elif never_weekend:
        reading.specs.append(RuleSpec(field="authorization.weekday", operator="not_in",
                                      value=list(WEEKDAYS[5:]), words=never_weekend.group(0).strip()))
    elif m := re.search(r"\b(?:only\s+)?(?:on\s+|at\s+)(?:the\s+)?weekends?(?:\s+only)?\b", text, re.IGNORECASE):
        reading.specs.append(RuleSpec(field="authorization.weekday", operator="in",
                                      value=list(WEEKDAYS[5:]), words=m.group(0).strip()))
    days = "|".join(_DAY_NAMES)
    if m := re.search(rf"\b(?:not|never)\s+on\s+(?P<d>(?:(?:{days})s?(?:\s*(?:,|or|and)\s*)?)+)", text, re.IGNORECASE):
        found = [_DAY_NAMES[d.lower()] for d in re.findall(days, m.group("d"), re.IGNORECASE)]
        reading.specs.append(RuleSpec(field="authorization.weekday", operator="not_in", value=found,
                                      words=m.group(0).strip()))
    for m in re.finditer(r"\b(?P<w>before|after|until|not after|not before)\s+(?P<h>\d{1,2})(?::(?P<mm>\d{2}))?"
                         r"\s*(?P<ap>am|pm|h|o'clock)?\b", text, re.IGNORECASE):
        hour = int(m.group("h")) % 24
        if (m.group("ap") or "").lower() == "pm" and hour < 12:
            hour += 12
        w = m.group("w").lower()
        op = {"before": "<", "not after": "<", "until": "<", "after": ">=", "not before": ">="}[w]
        reading.specs.append(RuleSpec(field="authorization.local_hour", operator=op, value=hour,
                                      words=m.group(0).strip()))


# --- Shops (C8, C9, C12 country, unverifiable) ---------------------------------------
def _shops(reading: _Reading, text: str) -> None:
    if m := _KNOWN_SHOP.search(text):
        regular = re.search(r"\bregularly\b", text[m.start(): m.end() + 12], re.IGNORECASE)
        reading.specs.append(RuleSpec(field=KNOWN_SHOP_FIELD, operator="=", value="true",
                                      words=m.group(0), source="inferred" if regular else "exact"))
    for m in _SHOP_PHRASE.finditer(text):
        kind = m.group("kind").strip()
        if re.fullmatch(r"(?:a|an|the|shops?|sellers?|stores?)", kind, re.IGNORECASE):
            continue
        if re.fullmatch(r"(?:(?:my|our|known|familiar|usual|regular)\s*)+", kind, re.IGNORECASE):
            continue  # "at known retailers", "from my usual shops": the known-shop rule (C9)
        said = text[m.start(): m.end()].strip()
        country = next((c for w, c in COUNTRY_WORDS.items() if re.search(rf"\b{w}\b", kind, re.IGNORECASE)), None)
        category = next((c for p, c in SHOP_KIND if p.search(kind)), None)
        if country:
            reading.specs.append(RuleSpec(field="merchant.merchant_country", operator="=", value=country, words=said))
        if category and not re.search(r"\b(?:official|authori[sz]ed)\b", kind, re.IGNORECASE):
            mapped = not re.search(rf"\b{category.split('_')[0]}", kind, re.IGNORECASE)  # "outdoor" -> sporting goods
            reading.specs.append(RuleSpec(field="merchant.merchant_category", operator="=", value=category,
                                          words=re.sub(r"^(?:from|at)\s+(?:a|an|the)\s+", "", said, flags=re.IGNORECASE),
                                          source="inferred" if mapped else "exact"))
        elif not country:
            reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=said, words=said))
            shop = re.sub(r"^(?:from|at)\s+", "", said, flags=re.IGNORECASE)
            reading.questions.append(f"Which shop is {shop}?")
    m = re.search(r"\b(?:at|from)\s+(?:the\s+)?supermarkets?\b", text, re.IGNORECASE)
    if m and not any(s.field == "merchant.merchant_category" for s in reading.specs):
        reading.specs.append(RuleSpec(field="merchant.merchant_category", operator="=", value="groceries",
                                      words=m.group(0), source="inferred"))
    for m in re.finditer(r"\b(?:shops?|sellers?|stores?)\s+in\s+(?P<c>[A-Z][a-z]+)", text):
        country = COUNTRY_WORDS.get(m.group("c").lower())
        if country and country in COUNTRY_NAMES:
            reading.specs.append(RuleSpec(field="merchant.merchant_country", operator="=", value=country,
                                          words=m.group(0)))


# --- Uncertainty (C11) ---------------------------------------------------------------
_DOUBT = r"(?:uncertain|unsure|in doubt|not sure|unclear|anything is unclear)"


def uncertainty_setting(text: str) -> tuple[str, list[str]]:
    """``ask`` (default), or ``decline`` when the customer said so. A request to approve
    under uncertainty is not taken (tighten only, P1): it stays ``ask`` with a question."""
    if re.search(rf"\b(?:decline|reject|refuse|cancel|skip)\b[^.]*\b{_DOUBT}"
                 rf"|\b(?:if|when)\s+{_DOUBT}[^.]*\b(?:decline|reject|don't buy|do not buy|skip|don't)\b",
                 text, re.IGNORECASE):
        return "decline", []
    if re.search(rf"\b(?:approve|buy it anyway|go ahead)\b[^.]*\b{_DOUBT}"
                 rf"|\b(?:if|when)\s+{_DOUBT}[^.]*\b(?:approve|go ahead|buy anyway)\b", text, re.IGNORECASE):
        return "ask", ["You asked to approve when something is uncertain; OneGuard will ask you instead. Is that OK?"]
    return "ask", []


# --- Same price as last time (C1 from history) ---------------------------------------
# ``on_fail: ask`` needs the customer to say so about a change ("ask me if anything
# changed", "if it differs, ask me"). "Ask me when uncertain" is C11, not this.
ASK_IF_CHANGED = re.compile(
    r"\bask me (?:if|when|whenever|in case)\s+(?:anything|something|the price|it|that|this|any of (?:it|this))"
    r"\s+(?:has\s+)?(?:changed|changes|differs|is different|goes up)\b"
    r"|\bif (?:anything|something|the price|a price|any price|prices|it|that) (?:has\s+)?"
    r"(?:changed|changes|change|differs|is different|goes up|go up)"
    r",?\s+(?:then\s+)?ask me\b",
    re.IGNORECASE,
)
_PRICE_SUBJECT = re.compile(r"\b(?:the|a|any) price\b|\bprices\b", re.IGNORECASE)


def _same_price(reading: _Reading, text: str, history, card_id: str) -> None:
    from oneguard.compiler.resolve import last_price

    m = re.search(r"\bsame (?:price|amount) as (?:last time|before|usual|last)\b", text, re.IGNORECASE)
    if not m:
        return
    found = last_price(history, card_id, reading.requested_item or text)
    if found is None:
        reading.questions.append(
            f'I found no earlier purchase for "{reading.requested_item or "this"}": what price should I expect?')
        return
    price, row = found
    reading.specs.append(RuleSpec(
        field="authorization.billing_amount_chf", operator="=", value=number(price), currency="CHF",
        scope="purchase", words=m.group(0), source="inferred",
        note=f"last paid at {row.merchant_name} on {row.timestamp:%d %b %Y}",
        value_from=f"history: last approved price at {row.merchant_id}"))


def _renew(reading: _Reading, text: str) -> None:
    """"Renew my X …, ask me if anything changed": the same shop as before, and a
    different one is a change to ask about (on_fail ask), never a decline."""
    m = re.search(r"\brenew\b", text, re.IGNORECASE)
    ask = ASK_IF_CHANGED.search(text)
    if m and ask and not any(s.field == KNOWN_SHOP_FIELD for s in reading.specs):
        reading.specs.append(RuleSpec(field=KNOWN_SHOP_FIELD, operator="=", value="true", words=m.group(0),
                                      source="inferred", on_fail="ask", note="the same shop as before",
                                      ask_clause=f"{m.group(0)} … {ask.group(0)}"))


def _ask_if_changed(reading: _Reading, text: str) -> None:
    """"…, ask me if anything changed" covers one rule: the one compiled from the clause
    just before it ("same price as last time"). That rule is recorded as asked about, so
    lint rejects ``on_fail: ask`` on any other rule, from either reading. When the clause
    holds no rule, or several ("buy groceries up to CHF 50"), none is: they decline.
    The parser itself asks only on a price taken from history; the renew rule records
    its own words (``_renew``)."""
    m = ASK_IF_CHANGED.search(text)
    before = [c for c in _clauses(text[: m.start()]) if c.lower() not in ("and", "but", "then")] if m else []
    if not m or not before or any(s.ask_clause == m.group(0) for s in reading.specs):  # _price_change took it
        return
    clause = before[-1]
    candidates = [
        i for i, s in enumerate(reading.specs)
        if s.words and s.words.lower() in clause.lower()
        and (s.field in MONEY_FIELDS or not _PRICE_SUBJECT.search(m.group(0)))
    ]
    if len(candidates) != 1 or reading.specs[candidates[0]].ask_clause:
        return
    spec = reading.specs[candidates[0]]
    ask = spec.value_from is not None and spec.value_from.startswith("history")
    reading.specs[candidates[0]] = spec.model_copy(update={
        "ask_clause": f"{clause}, {m.group(0)}", "on_fail": "ask" if ask else spec.on_fail})


_COUNT_PER_PERIOD = re.compile(
    rf"\b(?:at most\s+|no more than\s+|up to\s+|max(?:imum)?\s+)?{_NUM}\s+(?:[\w-]+\s+){{0,2}}?"
    r"(?:deliver(?:y|ies)|orders?|purchases?|bookings?|payments?)\s+(?:a|per|each|every)\s+(?:day|week|month)\b",
    re.IGNORECASE,
)


def _count_per_period(reading: _Reading, text: str) -> None:
    """"one delivery a day", "one meal delivery a day": a purchase count in a rolling
    window. Engine gap: no field counts purchases yet, so it is an unverifiable rule the
    customer is asked about (a period rule would be read as a CHF limit, P3)."""
    if m := _COUNT_PER_PERIOD.search(text):
        reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=m.group(0), words=m.group(0)))


_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def _stay(reading: _Reading, text: str) -> None:
    """A booking's place and dates ("a hotel in Munich for 3 nights from 10 September to
    13 September"): no field holds them, so each is an unverifiable rule the customer
    confirms. The nights also size the per-order question."""
    if not any(s.field == "items[].item_category" and "hotel" in s.value for s in reading.specs):
        return
    if m := re.search(r"\bhotels?\s+in\s+(?P<city>[A-Z][\w-]+)", text):
        said = f"in {m.group('city')}"
        reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=f"a hotel {said}", words=said))
    nights = re.search(rf"\bfor\s+(?P<n>{_NUM})\s+nights?\b", text, re.IGNORECASE)
    dates = re.search(rf"\bfrom\s+\d{{1,2}}\s+{_MONTHS}\s+to\s+\d{{1,2}}\s+{_MONTHS}\b", text, re.IGNORECASE)
    if nights or dates:
        said = " ".join(x.group(0) for x in (nights, dates) if x)
        reading.specs.append(RuleSpec(field="unverifiable", operator="=", value=said, words=said))
    if nights:
        reading.nights = _num(nights.group("n"))


def _price_change(reading: _Reading, text: str) -> None:
    """"If a price changes, ask me" with no single price to compare against (several
    subscriptions): engine gap, no field holds "the last price at this shop". It is an
    unverifiable rule that asks (``on_fail: ask``) and carries the clause that allows it."""
    m = ASK_IF_CHANGED.search(text)
    if not m or not _PRICE_SUBJECT.search(m.group(0)):
        return
    said = m.group(0)
    if re.search(r"\bsame (?:price|amount) as\b", text, re.IGNORECASE):
        return  # "same price as last time" is the price rule, found in history or asked for
    if any(s.ask_clause and said in s.ask_clause for s in reading.specs):
        return  # the clause covers a rule already ("same price as last time, ask me if ...")
    reading.specs.append(RuleSpec(field="unverifiable", operator="=", value="the price has not changed since last time",
                                  words=said, on_fail="ask", ask_clause=said))


def _amount_question(reading: _Reading) -> None:
    has_cap = any(s.field == "authorization.billing_amount_chf" and s.scope == "purchase" for s in reading.specs)
    if has_cap:
        return
    per_item = next((s for s in reading.specs if s.field == "items[].unit_price_chf" and s.operator in ("<=", "<")), None)
    if per_item and reading.nights and not reading.count:
        total = to_chf(per_item.value, per_item.currency) * reading.nights
        reading.questions.insert(0, (
            f"No per-order limit stated: is the order limit CHF {fmt_amount(total)} "
            f"({reading.nights} nights at CHF {fmt_amount(to_chf(per_item.value, per_item.currency))} each)?"))
        return
    if per_item and reading.count:
        total = to_chf(per_item.value, per_item.currency) * reading.count
        noun = reading.item_noun or "items"
        word = next((w for w, n in NUMBER_WORDS.items() if n == reading.count), str(reading.count))
        reading.questions.insert(0, (
            f"No per-order limit stated: is the order limit CHF {fmt_amount(total)} "
            f"({word} {noun} at CHF {fmt_amount(to_chf(per_item.value, per_item.currency))} each)?"))
        return
    if reading.item_noun and not reading.requested_item:
        what = f"this {reading.item_noun}"
    elif reading.requested_item:
        what = f"the {reading.requested_item}"
    else:
        what = "one purchase"
    reading.questions.insert(0, f"No amount stated: what is the most {what} may cost?")


def parse(instruction: str, history=None, card_id: str = "", today: date | None = None) -> ParsedDraft:
    """Read an English instruction into typed rules. Deterministic; never raises on text."""
    from oneguard.compiler.resolve import simulated_today

    text = " ".join(instruction.split())
    reading = _Reading()
    money = with_shared_currency(text)
    counted = _counted(money)
    for clause in _clauses(money):
        _amounts(reading, clause, counted)
    _item(reading, text)
    _blocked(reading, text)
    _details(reading, text)
    if today is None and history is not None:
        today = simulated_today(history, card_id)
    _terms(reading, text, today)
    _time(reading, text)
    _count_per_period(reading, text)
    _shops(reading, text)
    _stay(reading, text)
    if history is not None:
        _same_price(reading, text, history, card_id)
    _renew(reading, text)
    _ask_if_changed(reading, text)
    _price_change(reading, text)
    reading.uncertainty, extra = uncertainty_setting(text)
    reading.questions += extra
    _amount_question(reading)
    return finalize(
        instruction,
        reading.specs,
        uncertainty_policy=reading.uncertainty,  # type: ignore[arg-type]
        open_questions=reading.questions,
        requested_item=reading.requested_item,
        nothing_extra=reading.nothing_extra,
    )
