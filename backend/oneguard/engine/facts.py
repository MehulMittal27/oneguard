"""build_facts: turn one authorization event into trusted, typed Facts.

Serves rules.md M1-M3 (money), M6 (purchase time), P2/A2 (shop text is data) and the
extraction behind C6 (size), C7 (return window) and A6 (recurring).

What comes from where:
- Every field except three comes from the event's trusted structured fields.
- Shop text (``item_details``) is read ONLY by the allowlisted patterns below, and only
  for: size (EU numeric incl. half sizes, or letter S-XXXL), return window in days,
  recurring billing. Nothing else is ever read
  from text: never amounts, limits, permissions, merchants or categories (A2).
- An extracted value is a FactValue with ``source="regex"``. Not stated -> ``known=False``.
  Two different values in the same text -> ``known=False`` with the contradiction in
  ``detail`` (P3: missing or contradictory is never a pass).

Pure function: no I/O, no CSV, no network. ``history`` is only used for the catalogue
price range of each item (W6); familiarity (``merchant_known``) is set by the pipeline
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
# facts it may try and accept a model answer only when it is grounded in the shop text.
#
# Never tried: a contradiction (the shop said two things; picking one is a guess), a
# seller statement that the policy is not stated, and exchange/store-credit-only terms
# (a PM decision that they stay unknown). Only positive item facts are accepted, each
# with a verbatim quote that is in the line's text and contains the value; absence is
# never a fact, and amounts never come from text (A2).

TIER2_FIELDS: tuple[str, ...] = ("size_eu", "size_letter", "return_window_days", "recurring")
SELLER_STATED_UNKNOWN: tuple[str, ...] = (
    "return policy not stated by seller",
    "exchange or store credit only",
)
_TIER2_LETTER_SIZES = ("XS", "S", "M", "L", "XL", "XXL", "XXXL")
_TIER2_SIZE_EU_RANGE = (15.0, 55.0)
_TIER2_MAX_RETURN_DAYS = 365


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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def tier2_accept(facts: Facts, line_no: int, field: str, value: Any, quote: str) -> FactValue | None:
    """A model answer as a FactValue (``source="model"``), or None if it is not grounded.

    Grounded means: the fact may be tried, ``quote`` appears verbatim (case and spacing
    aside) in that line's ``item_details``, and the value is in the quote and plausible.
    """
    line = next((ln for ln in facts.items if ln.line_no == line_no), None)
    if line is None or not quote or not quote.strip():
        return None
    current = getattr(line, field, None)
    if current is None or not tier2_may_resolve(field, current):
        return None
    if _agent_directed(line.item_details) or _norm(quote) not in _norm(line.item_details):
        return None
    numbers = _numbers(quote)
    ok = False
    if field == "size_eu" and isinstance(value, int | float) and not isinstance(value, bool):
        v = float(value)
        low, high = _TIER2_SIZE_EU_RANGE
        ok = low <= v <= high and (v * 2).is_integer() and (v in numbers or float(int(v)) in numbers)
    elif field == "size_letter" and isinstance(value, str):
        v = value.strip().upper()
        # One-letter sizes must be capitals in the quote ("20 m" is metres, not size M).
        flags = re.IGNORECASE if len(v) > 1 else 0
        ok = v in _TIER2_LETTER_SIZES and bool(re.search(rf"(?<![A-Za-z]){v}(?![A-Za-z])", quote, flags))
        value = v
    elif field == "return_window_days" and isinstance(value, int) and not isinstance(value, bool):
        weeks = value / 7
        ok = 0 <= value <= _TIER2_MAX_RETURN_DAYS and (
            value in numbers or (weeks.is_integer() and weeks in numbers) or (value == 0 and bool(quote.strip())))
    elif field == "recurring":
        ok = value is True  # only a stated recurring charge; "not recurring" is absence
    if not ok:
        return None
    return current.__class__(value=value, known=True, source="model", detail=f'read by model from: "{quote.strip()}"')


def with_tier2_fact(facts: Facts, line_no: int, field: str, fact: FactValue) -> Facts:
    """Facts with one line's fact replaced by an accepted tier-2 value; the order-level
    return window is recomputed from the lines (strictest wins, as for regex facts)."""
    items = [ln.model_copy(update={field: fact}) if ln.line_no == line_no else ln for ln in facts.items]
    update: dict[str, Any] = {"items": items}
    if field == "return_window_days":
        update["return_window_days"] = order_return_window(facts.order_returnable, items)
    return facts.model_copy(update=update)
