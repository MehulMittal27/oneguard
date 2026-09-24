"""What both compiler paths produce, and how a typed rule is worded (§10 T1–T4).

The LLM path (llm.py) and the English fallback (parser.py) each turn an instruction
into ``RuleSpec`` rows. ``finalize`` gives every row a stable id, a plain-language
``text`` (limits use EXACTLY the wording of docs/api-contract.md §3.9) and a ``kind``,
and restates the rules in the Policy convenience fields. Texts are written here from
the typed rule, never copied from model output, so both paths read the same.

Vocabulary: docs/api-contract.md §3.3. Money: rules.md M1, M2, T4.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from oneguard.engine.types import Currency, Rule, RuleKind, RuleOperator

# rules.md M1: fixed rates to CHF.
FX_TO_CHF: dict[str, Decimal] = {
    "CHF": Decimal(1),
    "EUR": Decimal("0.95"),
    "GBP": Decimal("1.12"),
    "USD": Decimal("0.87"),
}

ITEM_CATEGORIES: tuple[str, ...] = (
    "books", "clothing", "cosmetics", "dining", "electronics", "food_delivery", "fuel",
    "gift_card", "groceries", "home_improvement", "hotel", "household", "membership",
    "sporting_goods", "subscriptions", "transport",
)
MERCHANT_CATEGORIES: tuple[str, ...] = (
    "books", "clothing", "dining", "electronics", "entertainment", "food_delivery", "fuel",
    "groceries", "health", "home_improvement", "hotel", "household", "kids_family",
    "pet_care", "photography", "software", "sporting_goods", "subscriptions",
    "sustainable_goods", "transport", "travel",
)
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
COUNTRY_NAMES: dict[str, str] = {
    "CH": "Switzerland", "DE": "Germany", "FR": "France", "IT": "Italy", "AT": "Austria",
    "NL": "the Netherlands", "GB": "the United Kingdom", "US": "the United States",
}
SIZE_LETTERS: tuple[str, ...] = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL")

ValueType = Literal["number", "text", "list", "date", "none"]

# field -> (value type, allowed operators, kind)
FIELDS: dict[str, tuple[ValueType, tuple[str, ...], RuleKind]] = {
    "authorization.billing_amount_chf": ("number", ("<", "<=", "="), "amount"),
    "items[].unit_price_chf": ("number", ("<", "<=", "="), "amount"),
    "items[].quantity": ("number", ("<", "<=", "=", ">=", ">"), "item"),
    "cart.quantity": ("number", ("<", "<=", "=", ">=", ">"), "item"),
    "items[].item_category": ("list", ("in", "not_in"), "item"),
    "items[].size_eu": ("number", ("=",), "item"),
    "items[].size_letter": ("text", ("=",), "item"),
    "order.return_window_days": ("number", (">=", ">"), "terms"),
    "order.order_returnable": ("text", ("=",), "terms"),
    "order.order_cancellable": ("text", ("=",), "terms"),
    "cart.recurring": ("text", ("=",), "item"),
    "merchant.merchant_category": ("text", ("=", "!="), "merchant"),
    "merchant.familiar_on_card": ("text", ("=",), "merchant"),
    "merchant.merchant_country": ("text", ("=", "!="), "merchant"),
    "authorization.delivery_by": ("date", ("<=",), "terms"),
    "authorization.weekday": ("list", ("in", "not_in"), "other"),
    "authorization.local_hour": ("number", ("<", "<=", ">=", ">"), "other"),
    "unverifiable": ("text", ("=",), "other"),
}
MONEY_FIELDS = frozenset({"authorization.billing_amount_chf", "items[].unit_price_chf"})
# The engine evaluates C9 through this field (engine/policy.py); api-contract §3.3
# names merchant.known_shop as the same check.
KNOWN_SHOP_FIELD = "merchant.familiar_on_card"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleSpec(_Model):
    """One restriction as read from the instruction, before it gets an id and text."""

    field: str
    operator: RuleOperator
    value: int | float | str | list[str]
    currency: Currency | None = None
    scope: Literal["purchase", "period"] | None = None
    period_days: int | None = Field(default=None, ge=1)
    words: str
    source: Literal["exact", "inferred"] = "exact"
    on_fail: Literal["decline", "ask"] = "decline"
    note: str | None = None  # extra wording for the check text (e.g. where a value came from)
    value_from: str | None = None  # set when the value is resolved, not stated (history, a date)


class ParsedDraft(_Model):
    """CompiledDraft without the dry-run: what lint and dryrun work on."""

    instruction: str
    rules: list[Rule]
    uncertainty_policy: Literal["ask", "decline"]
    open_questions: list[str]
    requested_item: str | None = None
    allowed_item_categories: list[str] | None = None
    blocked_item_categories: list[str] | None = None
    requires_known_shop: bool = False
    nothing_extra: bool = False
    shop_type: str | None = None
    resolved: dict[str, str] = Field(default_factory=dict)  # rule id -> where its value came from


# --- Money ---------------------------------------------------------------------------
def to_chf(value: Any, currency: str | None) -> Decimal:
    """M1 + M2: convert at the fixed rate, round half-even to 2 dp."""
    rate = FX_TO_CHF[currency or "CHF"]
    return (Decimal(str(value)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def fmt_amount(value: Any) -> str:
    """20 -> "20", 399.9 -> "399.90", 1200 -> "1,200" (§3.9 allows separators)."""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):,}"


def number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def human(code: str) -> str:
    return code.replace("_", " ")


def human_list(codes: list[str]) -> str:
    words = [human(c) for c in codes]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


def next_weekday(after: date, weekday: str) -> date:
    """The first ``weekday`` strictly after ``after`` ("by Friday" on a Friday = next week)."""
    target = WEEKDAYS.index(weekday)
    delta = (target - after.weekday() - 1) % 7 + 1
    return after + timedelta(days=delta)


# --- Wording -------------------------------------------------------------------------
def _limit_text(spec: RuleSpec, subject: str, tail: str) -> str:
    chf = to_chf(spec.value, spec.currency)
    bound = {"<=": "at or below", "<": "under", "=": "exactly"}[spec.operator]
    text = f"{subject} {bound} CHF {fmt_amount(chf)}{tail}"
    if spec.currency and spec.currency != "CHF":  # T4: converted and shown
        text += f" ({spec.currency} {fmt_amount(spec.value)} at {FX_TO_CHF[spec.currency]})"
    return text


def rule_text(spec: RuleSpec, requested_item: str | None = None) -> str:
    """Plain-language RuleCheck text for one typed rule."""
    f, op, v = spec.field, spec.operator, spec.value
    if f == "authorization.billing_amount_chf" and spec.scope == "period":
        text = _limit_text(spec, "Total", f" across any {spec.period_days or 7} days")
    elif f == "authorization.billing_amount_chf":
        text = _limit_text(spec, "Total", " per order")
    elif f == "items[].unit_price_chf":
        text = _limit_text(spec, "Each item", "")
    elif f in ("cart.quantity", "items[].quantity"):
        what = requested_item or "items"
        bound = {"=": "Exactly", "<=": "At most", "<": "Fewer than", ">=": "At least", ">": "More than"}[op]
        text = f"{bound} {fmt_amount(v)} {what}" + (" per cart line" if f == "items[].quantity" else "")
    elif f == "items[].item_category":
        cats = human_list(list(v))
        text = f"Only {cats}" if op == "in" else f"No {cats}"
    elif f == "items[].size_eu":
        text = f"Size {fmt_amount(v)}".replace(".50", ".5")
    elif f == "items[].size_letter":
        text = f"Size {v}"
    elif f == "order.return_window_days":
        text = f"Returns accepted for {fmt_amount(v)} days or more" if op == ">=" \
            else f"Returns accepted for more than {fmt_amount(v)} days"
    elif f == "order.order_returnable":
        text = "The order can be returned"
    elif f == "order.order_cancellable":
        text = "The order can be cancelled"
    elif f == "cart.recurring":
        text = "No recurring charges" if v == "false" else "Recurring billing expected"
    elif f == "merchant.merchant_category":
        shop = f"a {human(str(v))} shop"
        text = f'Only from {shop} ("{spec.words}")' if op == "=" else f"Not from {shop}"
    elif f == KNOWN_SHOP_FIELD:
        text = "Only shops you have bought from before"
    elif f == "merchant.merchant_country":
        where = COUNTRY_NAMES.get(str(v), str(v))
        text = f"Only shops in {where} ({v})" if op == "=" else f"No shops in {where} ({v})"
    elif f == "authorization.delivery_by":
        d = date.fromisoformat(str(v))
        text = f"Delivered on or before {d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"
    elif f == "authorization.weekday":
        days = list(v)
        if op == "in" and days == list(WEEKDAYS[:5]):
            text = "Only on weekdays (Mon–Fri)"
        elif op == "in" and days == list(WEEKDAYS[5:]):
            text = "Only at weekends (Sat–Sun)"
        else:
            names = ", ".join(d.capitalize() for d in days)
            text = f"Only on {names}" if op == "in" else f"Not on {names}"
    elif f == "authorization.local_hour":
        hour = int(v)
        text = {"<": f"Only before {hour:02d}:00", "<=": f"Only until {hour:02d}:59",
                ">=": f"Only from {hour:02d}:00", ">": f"Only after {hour:02d}:59"}[op]
        text += " (Swiss time)"
    elif f == "unverifiable":
        text = f'"{spec.words}" (no data can check this; you will be asked)'
    else:  # pragma: no cover - FIELDS is closed
        text = spec.words
    if spec.note:
        text += f" ({spec.note})"
    if spec.on_fail == "ask":
        text += "; ask me if it changed"
    return text


# --- Ids -----------------------------------------------------------------------------
def _base_id(spec: RuleSpec) -> str:
    f, op = spec.field, spec.operator
    if f == "authorization.billing_amount_chf":
        return "C2" if spec.scope == "period" else "C1"
    if f == "items[].item_category":
        return "C3" if op == "in" else "C4"
    if f in ("items[].size_eu", "items[].size_letter"):
        return "C6"
    if f in ("order.return_window_days", "order.order_returnable", "order.order_cancellable"):
        return "C7"
    if f == "merchant.merchant_category":
        return "C8"
    if f == KNOWN_SHOP_FIELD:
        return "C9"
    if f == "unverifiable":
        return "U1"
    return {
        "cart.quantity": "C12-qty", "items[].quantity": "C12-qty",
        "items[].unit_price_chf": "C12-price", "merchant.merchant_country": "C12-country",
        "authorization.delivery_by": "C12-delivery", "authorization.weekday": "C12-day",
        "authorization.local_hour": "C12-hour", "cart.recurring": "C12-recurring",
    }[f]


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    stem = base[:-1] if base == "U1" else base + "-"
    n = 2
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"


def _kind(spec: RuleSpec) -> RuleKind:
    if spec.field == "authorization.billing_amount_chf" and spec.scope == "period":
        return "period"
    return FIELDS[spec.field][2]


def to_rule(spec: RuleSpec, taken: set[str], requested_item: str | None = None) -> Rule:
    base = _base_id(spec)
    if spec.on_fail == "ask":  # "…ask me if anything changed": C1-same, C9-same (P2 answer key)
        base = f"{base}-same"
    rule_id = _unique(base, taken)
    taken.add(rule_id)
    uncertainty = None
    if spec.field == "unverifiable":
        uncertainty = "No data can check this, so OneGuard will ask you."
    elif spec.field == "authorization.delivery_by":
        uncertainty = "Unknown when the shop gives no delivery date."
    return Rule(
        id=rule_id,
        field=spec.field,
        operator=spec.operator,
        value=spec.value,
        currency=spec.currency,
        scope=spec.scope,
        period_days=spec.period_days,
        text=rule_text(spec, requested_item),
        source=spec.source,
        kind=_kind(spec),
        uncertainty=uncertainty,
        on_fail=spec.on_fail,
    )


def finalize(
    instruction: str,
    specs: list[RuleSpec],
    *,
    uncertainty_policy: Literal["ask", "decline"],
    open_questions: list[str],
    requested_item: str | None = None,
    nothing_extra: bool = False,
) -> ParsedDraft:
    """Give specs ids and texts, drop exact duplicates, restate the convenience fields."""
    seen: set[tuple] = set()
    unique: list[RuleSpec] = []
    for spec in specs:
        key = (spec.field, spec.operator, str(spec.value), spec.currency, spec.scope, spec.period_days)
        if key not in seen:
            seen.add(key)
            unique.append(spec)
    taken: set[str] = set()
    rules = [to_rule(s, taken, requested_item) for s in unique]
    resolved = {r.id: s.value_from for r, s in zip(rules, unique, strict=True) if s.value_from}

    def values(field: str, op: str) -> list[str] | None:
        out: list[str] = []
        for r in rules:
            if r.field == field and r.operator == op:
                out += list(r.value) if isinstance(r.value, list) else [str(r.value)]
        return list(dict.fromkeys(out)) or None

    shop = values("merchant.merchant_category", "=")
    questions = list(dict.fromkeys(q for q in open_questions if q))
    return ParsedDraft(
        instruction=instruction,
        rules=rules,
        uncertainty_policy=uncertainty_policy,
        open_questions=questions,
        requested_item=requested_item,
        allowed_item_categories=values("items[].item_category", "in"),
        blocked_item_categories=values("items[].item_category", "not_in"),
        # A known-shop rule that asks rather than declines ("renew", ask if it changed) is
        # not C9: a lookalike or new shop must stay a question (A7), not a decline.
        requires_known_shop=any(r.field == KNOWN_SHOP_FIELD and r.on_fail == "decline" for r in rules),
        nothing_extra=nothing_extra,
        shop_type=shop[0] if shop else None,
        resolved=resolved,
    )
