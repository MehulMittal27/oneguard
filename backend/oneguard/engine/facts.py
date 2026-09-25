"""build_facts: turn one authorization event into trusted, typed Facts.

Serves rules.md M1-M3 (money), M6 (purchase time), P2/A2 (shop text is data) and the
extraction behind C4 ("no alcohol"), C6 (size), C7 (return window) and A6 (recurring).

What comes from where:
- Every field except four comes from the event's trusted structured fields.
- Shop text (``item_details``; ``item_name`` for alcohol) is read ONLY by the allowlisted
  patterns below, and only for: size (EU numeric incl. half sizes, or letter S-XXXL),
  return window in days, recurring billing, alcohol (a lexicon, on lines whose category
  can be a drink, with the catalogue's text for the item id). Nothing else is ever read
  from text: never amounts, limits, permissions, merchants or categories (A2).
- An extracted value is a FactValue with ``source="regex"``. Not stated -> ``known=False``.
  Two different values in the same text -> ``known=False`` with the contradiction in
  ``detail`` (P3: missing or contradictory is never a pass).

Pure function: no I/O, no CSV, no network. ``history`` is only used for the catalogue
price range (W6) and text of each item; familiarity (``merchant_known``) is set by the pipeline
from the LedgerView before rules are evaluated.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from oneguard.engine.interfaces import register
from oneguard.engine.types import Facts, FactValue, HistoryIndex, ItemFacts

# Contract-aware sizes. The PM decisions (half sizes, letter sizes) need two contract
# changes (see the P2 contract PR). Until they land, half sizes are reported as unknown
# with a reason, and letter sizes are extracted but not attached to ItemFacts.
_SIZE_EU_DECIMAL = ItemFacts.model_fields["size_eu"].annotation is not FactValue[int]
_HAS_SIZE_LETTER = "size_letter" in ItemFacts.model_fields

ZURICH = ZoneInfo("Europe/Zurich")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# rules.md M1: fixed synthetic rates to CHF (data pack fx_rates.csv, dated 2026-08-01).
FX_TO_CHF: dict[str, Decimal] = {
    "CHF": Decimal("1.000000"),
    "EUR": Decimal("0.950000"),
    "GBP": Decimal("1.120000"),
    "USD": Decimal("0.870000"),
}
CENT = Decimal("0.01")

# --- Allowlisted extraction patterns (case-insensitive, English only) ----------------
# Contradictions are reported in FactValue.detail with the prefix below, so rules and
# explanations can flag them (a contradiction is unknown, never a guess).
CONTRADICTORY = "contradictory"

# EU (numeric) size: "size 43", "size 43.5", "size 43,5", "size 43½", "size EU 44", "EU 44",
# "41 EU". Half sizes are real sizes. Other decimals (43.2) are not read.
_EU_NUM = r"(?P<n>\d{2})(?P<half>[.,]5|\s?½|\s1/2)?(?!\d|[.,]\d)"
_SIZE_EU_PATTERNS = (
    re.compile(r"\bsize(?:\s*:\s*|\s+)(?:eu\s*)?" + _EU_NUM, re.IGNORECASE),
    re.compile(r"\beu\s*:?\s*" + _EU_NUM, re.IGNORECASE),
    re.compile(r"(?<![\d.,])(?P<n>\d{2})(?P<half>[.,]5|\s?½)?\s*eu\b", re.IGNORECASE),
)
# Letter size: "size S", "size XL", "size medium". A range ("size S-XL") is not read.
_LETTER_WORDS = {"small": "S", "medium": "M", "large": "L"}
_SIZE_LETTER = re.compile(
    r"\bsize(?:\s*:\s*|\s+)(XXXL|XXL|XL|XXS|XS|S|M|L|small|medium|large)\b(?![-/–])", re.IGNORECASE
)

# Return window in days: "returns accepted within 30 days" (the data pack's wording),
# "returns within 14 days", "30-day returns", "return within 2 weeks" (x7).
_RETURN_DAYS_PATTERNS = (
    re.compile(r"\breturns?\s+(?:accepted\s+)?within\s+(\d{1,3})\s*(days?|weeks?)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})[- ]?(day|week)s?\s+returns?\b", re.IGNORECASE),
)
# No returns -> 0 days.
_NO_RETURNS = re.compile(
    r"\bfinal\s+sale\b|\ball\s+sales\s+(?:are\s+)?final\b|\bno\s+returns?\b|\bno\s+refunds?\b"
    r"|\bnon[- ]?returnable\b|\breturns?\s+(?:are\s+)?not\s+accepted\b|\bcannot\s+be\s+returned\b"
    r"|\bnot\s+eligible\s+for\s+returns?\b",
    re.IGNORECASE,
)
# "Non-refundable" -> 0 days, unless the same clause is about shipping, delivery or a fee
# ("shipping costs non-refundable" sits next to normal return policies).
_NON_REFUNDABLE = re.compile(r"\bnon[- ]?refundable\b", re.IGNORECASE)
_FEE_WORDS = re.compile(r"\b(?:shipping|delivery|postage|handling|fee|fees|deposit)\b", re.IGNORECASE)
# Exchange or store credit only: no refund, but not a flat "no". Unknown; the customer decides.
_EXCHANGE_ONLY = re.compile(r"\bexchanges?\s+only\b|\bstore\s+credit\s+only\b", re.IGNORECASE)
_RETURNS_NOT_STATED = re.compile(r"\breturn\s+policy\s+not\s+stated\b", re.IGNORECASE)

# Recurring billing: billing language only. Bare "monthly"/"weekly" describe products
# ("Weekly grocery basket", "Monthly transit pass") and do NOT count.
_PERIOD = r"(?:month|week|year|quarter)"
_RECURRING = re.compile(
    r"\b(?:billed|charged|paid|payable|invoiced)\s+(?:monthly|weekly|quarterly|annually|yearly"
    r"|(?:every|each|per)\s+" + _PERIOD + r")\b"
    r"|\b(?:monthly|weekly|quarterly|annual|yearly)\s+(?:fee|charge|payment|billing|instal?ments?"
    r"|plan|membership)\b"
    r"|\b(?:per|each|every)\s+" + _PERIOD + r"\b"
    r"|/\s*(?:month|mo|year|yr|week|wk)\b"
    r"|\bauto[- ]?renew\w*|\brenews?\b|\brenewal\b|\brecurring\b|\bsubscriptions?\b",
    re.IGNORECASE,
)

# Alcohol (C4 "no alcohol"): an allowlisted lexicon in English, German, French and Italian,
# read only on lines whose trusted category can hold a drink. Order of reading:
# 1. non-drink uses of drink words are removed ("wine vinegar", "ginger beer", "Weintrauben");
# 2. alcohol-free wording is removed and remembered ("non-alcoholic", "alkoholfrei");
# 3. soft-drink wording is removed and remembered ("soft drinks", "Erfrischungsgetränke");
# 4. an alcohol word left over names alcohol; with alcohol-free wording beside it the line
#    says both, which is unknown ("non-alcoholic beer": the lexicon cannot tell which);
# 5. a bare drinks word left over ("drinks", "Getränke") is unknown unless steps 2-3 said
#    what the drinks are.
# Categories that cannot be a drink (a "wine rack" is household) are never alcohol.
_NOT_A_DRINK = frozenset({
    "books", "clothing", "cosmetics", "electronics", "fuel", "gift_card", "home_improvement", "hotel",
    "household", "photography", "sporting_goods", "transport", "travel",
})
_NOT_ALCOHOL_USES = re.compile(
    r"\b(?:red\s+|white\s+)?wine\s+vinegar\b|\bweinessig\b|\bvinaigre\s+de\s+vin\b|\baceto\s+di\s+vino\b"
    r"|\bginger\s+(?:beer|ale)\b|\broot\s+beer\b|\bingwerbier\b|\bweintrauben?\b|\bwine\s+gums?\b"
    r"|\bweingummis?\b",
    re.IGNORECASE,
)
_ALCOHOL_FREE = re.compile(
    r"\b(?:non|no|zero)[- ]?alcoholic\b|\balcohol[- ]free\b|\b(?:no|zero|without)\s+alcohol\b"
    r"|\bde-?alcoholi[sz]ed\b|(?<![\d.,])0[.,]0\s?%|\balkoholfrei\w*|\bohne\s+alkohol\b"
    r"|\bsans\s+alcool\b|\bnon[- ]alcoolis\w*|\banalcolic\w*|\bsenza\s+alcol\w*|\bsin\s+alcohol\b",
    re.IGNORECASE,
)
_SOFT_DRINKS = re.compile(
    r"\bsoft[- ]?drinks?\b|\bsoftgetränke?\b|\berfrischungsgetränke?\b|\bboissons?\s+(?:gazeuses|fraîches)\b"
    r"|\bbibite\s+gassate\b",
    re.IGNORECASE,
)
_ALCOHOL = re.compile(
    r"\balcohol(?:ic)?\b|\bwines?\b|\bspirits\b|\bliquors?\b|\bliqueurs?\b|\bbeers?\b|\bales?\b|\blagers?\b"
    r"|\bciders?\b|\bchampagnes?\b|\bprosecco\b|\bcava\b|\bwhisk(?:e?y|ies)\b|\bvodkas?\b|\bgin\b|\brums?\b"
    r"|\btequila\b|\bmezcal\b|\bbrand(?:y|ies)\b|\bcognac\b|\barmagnac\b|\bsherry\b|\bvermouth\b"
    r"|\bschnap(?:p?s)\b|\babsinthe?\b|\bbourbon\b|\bcocktails?\b|\bhard\s+seltzers?\b|\balcopops?\b|\bbooze\b"
    r"|\bsangria\b|\baperol\b|\bcampari\b|\blimoncello\b|\bpils(?:ner)?\b|\bstout\b"
    r"|\balkohol\w*|\b(?:rot|weiss|weiß|glüh|schaum|dessert|süss|süß|land|tafel|apfel|obst|jung|perl|brannt)?"
    r"wein(?:e|es)?\b|\b\w*biere?\b|\bspirituosen\b|\bschnäpse\b|\blikör(?:e)?\b|\bsekt\b|\bgrappa\b"
    r"|\bkirsch(?:wasser)?\b|\bobstbrand\b|\bwodka\b"
    r"|\balcool(?:is\w*)?\b|\bvins?\b|\bbières?\b|\bspiritueux\b|\bcidres?\b|\beau[- ]de[- ]vie\b"
    r"|\balcol\w*|\bvin[oi]\b|\bbirr[ae]\b|\bliquor[ei]\b|\bspumante\b",
    re.IGNORECASE,
)
_DRINKS = re.compile(
    r"\bdrinks?\b|\bbeverages?\b|\bgetränke?\b|\bboissons?\b|\bbevand[ae]\b|\bbibit[ae]\b"
    r"|\bap[ée]ritif\w*|\baperitivo\b|\bap[ée]ro\b|\bminibar\b|\bpunch\b",
    re.IGNORECASE,
)


# --- Money ---------------------------------------------------------------------------
def to_chf(amount: Any, currency: str) -> float:
    """Convert an amount in ``currency`` to CHF, half-even to 2 dp (M1, M2).

    Raises ValueError for a currency outside the fixed table; the caller treats the
    value as unknown rather than guessing a rate.
    """
    rate = FX_TO_CHF.get(currency)
    if rate is None:
        raise ValueError(f"no fixed rate for currency {currency!r}")
    return float((Decimal(str(amount)) * rate).quantize(CENT, rounding=ROUND_HALF_EVEN))


def _money(value: Any) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN))


# --- Extraction ----------------------------------------------------------------------
def _clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[;.\n]", text or "") if c.strip()]


def extract_size_eu(text: str) -> FactValue:
    found: set[float] = set()
    for p in _SIZE_EU_PATTERNS:
        for m in p.finditer(text or ""):
            found.add(int(m.group("n")) + (0.5 if m.group("half") else 0.0))
    kind = FactValue[float] if _SIZE_EU_DECIMAL else FactValue[int]
    if not found:
        return kind(known=False, source="regex", detail="EU size not stated")
    if len(found) > 1:
        return kind(known=False, source="regex",
                    detail=f"{CONTRADICTORY} sizes in shop text: {sorted(found)}")
    size = found.pop()
    if size.is_integer():
        return kind(value=size if _SIZE_EU_DECIMAL else int(size), known=True, source="regex",
                    detail=f"size {size:g}")
    if _SIZE_EU_DECIMAL:
        return kind(value=size, known=True, source="regex", detail=f"size {size:g}")
    return kind(known=False, source="regex",
                detail=f"half size {size:g} stated; whole-number size field cannot hold it yet")


def extract_size_letter(text: str) -> FactValue[str]:
    found = {
        _LETTER_WORDS.get(m.group(1).lower(), m.group(1).upper())
        for m in _SIZE_LETTER.finditer(text or "")
    }
    if not found:
        return FactValue[str](known=False, source="regex", detail="letter size not stated")
    if len(found) > 1:
        return FactValue[str](
            known=False, source="regex",
            detail=f"{CONTRADICTORY} sizes in shop text: {sorted(found)}",
        )
    size = found.pop()
    return FactValue[str](value=size, known=True, source="regex", detail=f"size {size}")


def extract_return_window_days(text: str) -> FactValue[int]:
    text = text or ""
    values: set[int] = set()
    for p in _RETURN_DAYS_PATTERNS:
        for m in p.finditer(text):
            n = int(m.group(1))
            values.add(n * 7 if m.group(2).lower().startswith("week") else n)
    if _NO_RETURNS.search(text):
        values.add(0)
    if any(_NON_REFUNDABLE.search(c) and not _FEE_WORDS.search(c) for c in _clauses(text)):
        values.add(0)
    exchange_only = bool(_EXCHANGE_ONLY.search(text))

    if len(values) > 1 or (exchange_only and values):
        stated = sorted(values) + (["exchange only"] if exchange_only else [])
        return FactValue[int](
            known=False, source="regex",
            detail=f"{CONTRADICTORY} return terms in shop text: {stated}",
        )
    if values:
        days = values.pop()
        detail = "no returns (final sale / non-refundable)" if days == 0 else f"returns within {days} days"
        return FactValue[int](value=days, known=True, source="regex", detail=detail)
    if exchange_only:
        return FactValue[int](known=False, source="regex",
                              detail="exchange or store credit only; no refund stated")
    if _RETURNS_NOT_STATED.search(text):
        return FactValue[int](known=False, source="regex", detail="return policy not stated by seller")
    return FactValue[int](known=False, source="regex", detail="return terms not stated")


def extract_recurring(text: str) -> FactValue[bool]:
    m = _RECURRING.search(text or "")
    if m:
        return FactValue[bool](value=True, known=True, source="regex",
                               detail=f"recurring billing stated ({m.group(0).lower()})")
    return FactValue[bool](known=False, source="regex", detail="recurring billing not stated")


def _alcohol_reading(text: str) -> tuple[str, str, str]:
    """Steps 1-5 above on one text: ``(verdict, word, qualifier)``. ``verdict`` is
    ``alcohol``, ``both``, ``drinks``, ``free`` (alcohol-free or soft drinks stated) or
    ``none``; ``word`` the alcohol or drinks word read, ``qualifier`` the alcohol-free or
    soft-drink wording."""
    text = _NOT_ALCOHOL_USES.sub(" ", text or "")
    free = [m.group(0) for m in _ALCOHOL_FREE.finditer(text)]
    text = _ALCOHOL_FREE.sub(" ", text)
    soft = [m.group(0) for m in _SOFT_DRINKS.finditer(text)]
    text = _SOFT_DRINKS.sub(" ", text)
    qualifier = (free + soft)[0].lower() if free or soft else ""
    if m := _ALCOHOL.search(text):
        return ("both" if free else "alcohol"), m.group(0).lower(), qualifier
    if m := _DRINKS.search(text):
        return ("free" if qualifier else "drinks"), m.group(0).lower(), qualifier
    return ("free" if qualifier else "none"), "", qualifier


def extract_contains_alcohol(category: str, text: str, catalogue: str | None = None) -> FactValue[bool]:
    """Is this cart line an alcoholic drink? ``text`` is the shop's item name and details
    (untrusted, read only by the lexicon), ``catalogue`` the catalogue's name and
    description for the item id. The catalogue naming alcohol wins over anything the shop
    says (a shop cannot rename wine into "drinks"); otherwise both texts are read together.
    Known False when the category cannot be a drink or neither text names alcohol or
    drinks; unknown when drinks are named without saying which, or alcohol and
    alcohol-free are named together (P3). ``detail`` completes "<item name> …"."""
    if category.lower() in _NOT_A_DRINK:
        return FactValue[bool](value=False, known=True, source="event",
                               detail=f"is {category.replace('_', ' ')}, not a drink")
    verdict, word, _ = _alcohol_reading(catalogue or "")
    if verdict == "alcohol":
        return FactValue[bool](value=True, known=True, source="history",
                               detail=f'is listed as alcohol in the catalogue ("{word}")')
    verdict, word, qualifier = _alcohol_reading(f"{text}\n{catalogue or ''}")
    if verdict == "alcohol":
        return FactValue[bool](value=True, known=True, source="regex", detail=f'says "{word}"')
    if verdict == "both":
        return FactValue[bool](known=False, source="regex", detail=f'says "{word}" and "{qualifier}", so it may contain alcohol')
    if verdict == "drinks":
        return FactValue[bool](known=False, source="regex",
                               detail=f'says "{word}" but not whether they contain alcohol')
    if verdict == "free":
        return FactValue[bool](value=False, known=True, source="regex", detail=f'says "{qualifier}"')
    return FactValue[bool](value=False, known=True, source="regex", detail="names no alcohol")


def order_return_window(order_returnable: str, lines: list[ItemFacts]) -> FactValue[int]:
    """Order-level return window.

    - ``order_returnable == "false"`` -> 0 days, source event (api-contract §3.3).
    - Otherwise the strictest (smallest) window any line states.
    - Lines that state nothing are ignored when another line states a window
      (e.g. an add-on line with no return terms); if no line states one -> unknown.
    Rule C7 still applies "stricter source wins" between this and order_returnable.
    """
    if order_returnable == "false":
        return FactValue[int](value=0, known=True, source="event", detail="order not returnable")
    stated = [ln.return_window_days for ln in lines if ln.return_window_days.known]
    contradictory = [ln.return_window_days for ln in lines
                     if not ln.return_window_days.known
                     and ln.return_window_days.detail.startswith(CONTRADICTORY)]
    if contradictory:
        return FactValue[int](known=False, source="regex", detail=contradictory[0].detail)
    if stated:
        best = min(stated, key=lambda f: f.value)  # strictest
        return FactValue[int](value=best.value, known=True, source=best.source, detail=best.detail)
    not_stated = [ln.return_window_days.detail for ln in lines]
    detail = ("return policy not stated by seller"
              if "return policy not stated by seller" in not_stated else "return terms not stated")
    return FactValue[int](known=False, source="regex", detail=detail)


# --- Event parsing -------------------------------------------------------------------
def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)  # Python 3.11+ parses the trailing "Z"


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _authorization(event: dict) -> dict:
    """Accept the full event (``{"authorization": ...}``) or the envelope's ``data``."""
    if "authorization" in event:
        return event["authorization"]
    if "data" in event and "authorization" in event["data"]:
        return event["data"]["authorization"]
    raise ValueError("event has no authorization object")


def _item_facts(line: dict, history: HistoryIndex | None) -> ItemFacts:
    details = line.get("item_details") or ""
    currency = line["currency"]
    unit_chf = to_chf(line["unit_price"], currency)  # schema restricts currency to the 4 fixed rates
    price_range = history.item_price_range(line["item_id"]) if history is not None else None
    catalogue = history.catalogue_item_text(line["item_id"]) if history is not None else None
    extra: dict[str, Any] = {}
    if _HAS_SIZE_LETTER:
        extra["size_letter"] = extract_size_letter(details)
    if price_range is not None:
        extra.update(unit_price_min_chf=price_range[0], unit_price_typical_chf=price_range[1],
                     unit_price_max_chf=price_range[2])
    return ItemFacts(
        line_no=int(line["line_no"]),
        item_id=line["item_id"],
        item_name=line["item_name"],
        item_category=line["item_category"],
        quantity=int(line["quantity"]),
        unit_price=_money(line["unit_price"]),
        currency=currency,
        unit_price_chf=unit_chf,
        item_details=details,  # untrusted; kept verbatim for evidence only
        size_eu=extract_size_eu(details),
        return_window_days=extract_return_window_days(details),
        recurring=extract_recurring(details),
        contains_alcohol=extract_contains_alcohol(
            line["item_category"], f"{line['item_name']}\n{details}", catalogue),
        **extra,
    )


@register("build_facts")
def build_facts(event: dict, history: HistoryIndex | None = None) -> Facts:
    auth = _authorization(event)
    merchant = auth["merchant"]
    ts = _parse_ts(auth["timestamp"])
    local = ts.astimezone(ZURICH)
    items = [_item_facts(line, history) for line in sorted(auth["items"], key=lambda x: x["line_no"])]

    billing = auth.get("billing_amount_chf")
    billing_chf = _money(billing) if billing is not None else to_chf(auth["amount"], auth["currency"])

    return Facts(
        authorization_id=auth["authorization_id"],
        source_authorization_id=auth.get("source_authorization_id") or auth["authorization_id"],
        timestamp=ts,
        local_weekday=WEEKDAYS[local.weekday()],
        local_hour=local.hour,
        amount=_money(auth["amount"]),
        currency=auth["currency"],
        billing_amount_chf=billing_chf,  # already includes delivery (M3)
        items=items,
        merchant_id=merchant["merchant_id"],
        merchant_name=merchant["merchant_name"],  # untrusted; display and A7 only
        merchant_category=merchant["merchant_category"],
        merchant_country=merchant["merchant_country"],
        merchant_city=merchant.get("merchant_city") or None,
        merchant_mcc=str(merchant.get("merchant_mcc") or ""),
        merchant_recurring_capable=str(merchant.get("recurring_capable", "false")).lower() == "true",
        device_id=auth.get("customer_device_id") or "",
        recent_attempt_count_10m=int(auth.get("recent_attempt_count_10m") or 0),
        order_returnable=auth["order_returnable"],
        order_cancellable=auth["order_cancellable"],
        delivery_by=_parse_date(auth.get("delivery_by")),
        related_authorization_id=auth.get("related_authorization_id"),
        related_status=auth.get("related_authorization_status"),
        return_window_days=order_return_window(auth["order_returnable"], items),
        authority_status=auth.get("authority_status", "active"),
        card_status_at_attempt=auth.get("card_status_at_attempt", "active"),
    )


# --- Tier 2 guardrails (rules.md §4a; P2 flag R2) ------------------------------------
# Tier 2 (P4) may ask a model to read a fact the English patterns above missed, e.g.
# German shop text ("Grösse 42", "Rückgabe innerhalb 30 Tagen"). These helpers say which
# facts it may try; tier2.py checks each answer is grounded next to its label word.
#
# Never tried: a contradiction (the shop said two things; picking one is a guess), a
# seller statement that the policy is not stated, and exchange/store-credit-only terms
# (a PM decision that they stay unknown). Amounts never come from text (A2).

# Recurring billing is not here: it stays tier-1 regex only (CLAUDE.md rule 2), so a model
# can never add or remove a recurring charge.
TIER2_FIELDS: tuple[str, ...] = ("size_eu", "size_letter", "return_window_days")
SELLER_STATED_UNKNOWN: tuple[str, ...] = (
    "return policy not stated by seller",
    "exchange or store credit only",
)


def tier2_may_resolve(field: str, fact: FactValue) -> bool:
    """True if tier 2 may try to read this item fact from the shop text."""
    if field not in TIER2_FIELDS or fact.known:
        return False
    detail = (fact.detail or "").lower()
    if detail.startswith(CONTRADICTORY):
        return False
    return not any(detail.startswith(s) for s in SELLER_STATED_UNKNOWN)


def _agent_directed(text: str) -> bool:
    """A1: text aimed at the agent is never fed to a model (P5's detector)."""
    try:
        from oneguard.engine.protections import agent_directed_spans
    except ImportError:  # P5 module missing: be safe, treat any imperative-looking text as flagged
        return bool(re.search(r"\b(ignore|approve|system\s*:)", text or "", re.IGNORECASE))
    return bool(agent_directed_spans(text or ""))


def tier2_candidates(facts: Facts) -> list[tuple[int, str, str]]:
    """``(line_no, field, text)`` for every fact tier 2 may try; ``text`` is the only text
    the model may read (that line's ``item_details``). Lines with agent-directed text are
    skipped entirely."""
    out = []
    for line in facts.items:
        if not line.item_details or _agent_directed(line.item_details):
            continue
        for field in TIER2_FIELDS:
            fact = getattr(line, field, None)
            if fact is not None and tier2_may_resolve(field, fact):
                out.append((line.line_no, field, line.item_details))
    return out

