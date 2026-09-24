"""evaluate_rules: check one purchase's Facts against the customer's confirmed Policy.

Serves rules.md §5 (C1-C12), P3 (missing is never a pass) and D1 (a clear violation is a
fail; the uncertainty setting only applies to unknowns, and is applied by decide.py).

Two kinds of input describe what the customer allowed, and both are checked:
- ``policy.rules``: typed rules (field / operator / value) using the field vocabulary in
  api-contract.md §3.3. Each is checked generically by its field.
- Structured flags on the Policy (requested_item, allowed/blocked categories, shop_type,
  requires_known_shop, nothing_extra). A flag is only checked when the customer stated
  it, and only when no typed rule already covers the same thing.

Step 1 (rules.md §4): policy active, authority active, card not blocked are reported
first, as RuleResults with the ids in ``types.STEP1_RULE_IDS``.

``Rule.on_fail`` (P2 contract request): ``decline`` (default) - a broken rule fails;
``ask`` - a broken rule is unknown, so the customer's uncertainty setting applies ("same
price as last time, ask me if anything changed"). Restrictions no data can check are
rules with a field outside the vocabulary (e.g. ``unverifiable``): always unknown.

Every result is pass / fail / unknown, with a readable ``detail`` (what was seen) and,
for a fail, a ``counterfactual`` (what would have passed). Explanations are built from
these strings, so they are written for the customer.

Period limits (C2, ``scope == "period"``: spend, or a purchase count with the field
``cart.purchases_in_period``) and remembered confirmations need the ledger,
which ``evaluate_rules`` does not receive. ``add_ledger_results(rules, facts, policy,
ledger)`` adds them: the pipeline calls it after ``evaluate_rules`` (and after tier 2), so
decide.py and explain.py both see them (P2 request to P1: one line in pipeline.py). Until
then decide.py treats a period rule with no result as unknown (P3).

Pure function: no I/O, no CSV, no history lookups. Familiarity (C9) is read from
``facts.merchant_known``, set by the pipeline from the LedgerView; ``add_ledger_results``
makes it unknown when the customer has no purchase history yet (rules.md C9).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from oneguard.engine.facts import CONTRADICTORY, ZURICH, to_chf
from oneguard.engine.interfaces import register
from oneguard.engine.ledger_base import confirmation_key, shop_confirmation_key
from oneguard.engine.types import (
    STEP1_RULE_IDS,
    Facts,
    FactValue,
    ItemFacts,
    LedgerView,
    Policy,
    Rule,
    RuleResult,
)

# Marker decide.py uses for M5: a period limit that fails only because of reservations.
RESERVATION_ONLY = "fails only because of pending reservations"
CONFIRMED = "You confirmed this"  # detail prefix of a rule passed from a remembered answer
# api-contract §3.3: how many purchases the period window may hold (scope period).
COUNT_FIELD = "cart.purchases_in_period"
# C9 when the customer has no approved purchase at all, in history or in this run.
NO_HISTORY = ("You have no purchase history yet, so I can't tell whether you've used this shop"
              " - approve once and I'll remember it")
NO_HISTORY_COUNTERFACTUAL = "Would approve at this shop next time if you approve this one"
KNOWN_SHOP_FIELDS = ("merchant.known_shop", "merchant.familiar_on_card")  # api-contract §3.3: the same check
ALCOHOL_FIELD = "items[].contains_alcohol"  # api-contract §3.3: C4 "no alcohol", every cart line

_NUMERIC_FIELDS = {
    "authorization.billing_amount_chf",
    "items[].unit_price_chf",
    "items[].quantity",
    "cart.quantity",
    "items[].size_eu",
    "order.return_window_days",
    "authorization.local_hour",
}
_MONEY_FIELDS = {"authorization.billing_amount_chf", "items[].unit_price_chf"}
# Item fields that describe the product itself: checked on the lines that are the
# requested item (an add-on such as a protection plan has no size).
_PRODUCT_FIELDS = {"items[].size_eu", "items[].size_letter"}

_LABELS = {
    "authorization.billing_amount_chf": "order total",
    "items[].unit_price_chf": "item price",
    "items[].quantity": "quantity",
    "cart.quantity": "number of items",
    "items[].item_category": "item type",
    ALCOHOL_FIELD: "alcohol",
    "items[].size_eu": "size",
    "items[].size_letter": "size",
    "order.return_window_days": "return window",
    "order.order_returnable": "returnable",
    "order.order_cancellable": "cancellable",
    "merchant.merchant_category": "shop type",
    "merchant.merchant_country": "shop country",
    "merchant.familiar_on_card": "shop you have bought from before",
    "merchant.known_shop": "shop you have bought from before",
    "cart.recurring": "recurring billing",
    "authorization.delivery_by": "delivery date",
    "authorization.weekday": "day of the week",
    "authorization.local_hour": "time of day",
}
_OP_WORDS = {"<": "under", "<=": "at or below", "=": "", "!=": "not", ">": "over",
             ">=": "at least", "in": "one of", "not_in": "not"}
_STOPWORDS = {"the", "a", "an", "my", "i", "of", "for", "in", "on", "to", "and", "with", "chose", "picked"}


# --- Small helpers -------------------------------------------------------------------
def _fv(value: Any, source: str = "event", detail: str = "") -> FactValue:
    return FactValue(value=value, known=value is not None, source=source, detail=detail)


def _money(x: Any) -> str:
    return f"CHF {Decimal(str(x)):.2f}"


def _fmt(field: str, value: Any) -> str:
    if value is None:
        return "not stated"
    if isinstance(value, list):
        return ", ".join(_human(v) for v in value)
    if field in _MONEY_FIELDS:
        return _money(value)
    if field == "order.return_window_days":
        return "no returns" if value == 0 else f"{value:g} days"
    if isinstance(value, float):
        return f"{value:g}"
    return _human(value)


def _human(value: Any) -> str:
    """Catalogue codes read as words: sporting_goods -> sporting goods."""
    return str(value).replace("_", " ")


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOPWORDS}


def matches_requested_item(line: ItemFacts, requested_item: str) -> bool:
    """A line is the requested item when every meaningful word of the request appears in
    the item name ("27-inch monitor" matches "27-inch computer monitor"; "road-running
    shoes" does not match "Trail-running shoes"). Plurals are folded."""
    wanted = _tokens(requested_item)
    return bool(wanted) and wanted <= _tokens(line.item_name)


def _requested_lines(facts: Facts, policy: Policy) -> list[ItemFacts]:
    if policy.requested_item:
        return [ln for ln in facts.items if matches_requested_item(ln, policy.requested_item)]
    return list(facts.items)


def _rule_value_chf(rule: Rule) -> Any:
    """T4: a limit stated in another currency is converted to CHF before comparing."""
    if rule.field in _MONEY_FIELDS and rule.currency and rule.currency != "CHF" and rule.value is not None:
        return to_chf(rule.value, rule.currency)
    return rule.value


def _compare(fact: Any, op: str, target: Any) -> bool:
    if op in ("in", "not_in"):
        members = {str(v).lower() for v in (target if isinstance(target, list) else [target])}
        inside = _norm(fact).lower() in members
        return inside if op == "in" else not inside
    if isinstance(fact, (int, float, Decimal)) and not isinstance(fact, bool):
        a, b = Decimal(str(fact)), Decimal(str(target))
    elif hasattr(fact, "isoformat"):
        a, b = fact.isoformat(), str(target)
    else:
        a, b = str(fact).lower(), str(target).lower()
    return {"<": a < b, "<=": a <= b, "=": a == b, "!=": a != b, ">": a > b, ">=": a >= b}[op]


def _norm(fact: Any) -> str:
    if isinstance(fact, bool):
        return "true" if fact else "false"
    if isinstance(fact, float) and fact.is_integer():
        return str(int(fact))
    return str(fact)


# --- Fact lookup by field ------------------------------------------------------------
def _facts_for(field: str, facts: Facts, policy: Policy) -> tuple[list[FactValue], list[ItemFacts | None]] | None:
    """Return (values to check, the cart line behind each value: None for an order fact),
    or None if the field is not known."""
    lines = facts.items
    product_lines = _requested_lines(facts, policy)
    known_shop = FactValue(
        value=None if facts.merchant_known is None else ("true" if facts.merchant_known else "false"),
        known=facts.merchant_known is not None, source="history",
        detail="" if facts.merchant_known is not None else "shop familiarity not available")
    single = {
        "authorization.billing_amount_chf": _fv(facts.billing_amount_chf),
        "merchant.merchant_category": _fv(facts.merchant_category),
        "merchant.merchant_country": _fv(facts.merchant_country),
        # api-contract §3.3: customer level (Q7); familiar_on_card is an alias.
        "merchant.known_shop": known_shop,
        "merchant.familiar_on_card": known_shop,
        "order.return_window_days": facts.return_window_days,
        "order.order_returnable": _order_term(facts.order_returnable),
        "order.order_cancellable": _order_term(facts.order_cancellable),
        "authorization.delivery_by": _fv(facts.delivery_by, detail="" if facts.delivery_by else "delivery date not stated"),
        "authorization.weekday": _fv(facts.local_weekday),
        "authorization.local_hour": _fv(facts.local_hour),
        # Total quantity of the requested item across all cart lines (all lines if no
        # requested item): "two tickets" is 1 line x 2 or 2 lines x 1.
        "cart.quantity": _fv(sum(ln.quantity for ln in product_lines)),
        "cart.recurring": _cart_recurring(facts),
    }
    if field in single:
        return [single[field]], [None]
    per_line = {
        "items[].item_category": lambda ln: _fv(ln.item_category),
        "items[].unit_price_chf": lambda ln: _fv(ln.unit_price_chf),
        "items[].quantity": lambda ln: _fv(ln.quantity),
        "items[].size_eu": lambda ln: ln.size_eu,
        ALCOHOL_FIELD: lambda ln: ln.contains_alcohol,
        "items[].size_letter": lambda ln: getattr(ln, "size_letter", None) or FactValue(
            known=False, source="regex", detail="letter sizes are not in the contract yet"),
    }
    if field in per_line:
        chosen = product_lines if field in _PRODUCT_FIELDS else lines
        if not chosen:
            return [FactValue(known=False, source="event", detail="not checked, the cart does not contain the requested item")], [None]
        return [per_line[field](ln) for ln in chosen], list(chosen)
    return None


def _order_term(raw: str) -> FactValue:
    if raw in ("true", "false"):
        return _fv(raw)
    return FactValue(value=None, known=False, source="event",
                     detail="not stated" if raw == "unknown" else "does not apply to this order")


def _cart_recurring(facts: Facts) -> FactValue:
    """api-contract §3.3: "true" if any line is subscriptions/membership or its text states
    recurring billing; otherwise "false" (categories are trusted, so this is known)."""
    for ln in facts.items:
        if ln.item_category in ("subscriptions", "membership"):
            return FactValue(value="true", known=True, source="event", detail=f"{ln.item_name} is a {ln.item_category} item")
        if ln.recurring.known and ln.recurring.value:
            return FactValue(value="true", known=True, source="regex", detail=ln.recurring.detail)
    return FactValue(value="false", known=True, source="event", detail="no recurring billing in the cart")


# --- Message templates ---------------------------------------------------------------
# One template per field (rules.md §9). A broken rule's ``detail`` is one clause, the one
# the message says after "Declined CHF <amount>: ", and its ``counterfactual`` is the
# "Would approve ..." sentence the API returns on its own; neither has a final stop
# (explain.py adds it). The flag checks below write the same clauses, so a typed rule and
# a flag on the same thing read alike.
ASK_CHANGED = "you asked to be asked if it changed"
"""Appended to a broken ``on_fail: ask`` rule's clause; the rule is unknown, not a fail."""

_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
              "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}  # fmt: skip


def _values_of(rule: Rule) -> list[Any]:
    return rule.value if isinstance(rule.value, list) else [rule.value]


def _joined(words: list[str], conjunction: str) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {conjunction} {words[-1]}"


def _either(words: list[str]) -> str:
    return _joined(words, "or")


def _both(words: list[str]) -> str:
    return _joined(words, "and")


def _a(noun: str) -> str:
    return f"{'an' if noun[:1].lower() in 'aeiou' else 'a'} {noun}"


def _day(code: Any) -> str:
    return _DAY_NAMES.get(str(code).lower(), _human(code))


def _country(code: Any) -> str:
    # Imported here: warnings imports protections, which imports this module.
    from oneguard.engine.warnings import country_name

    return country_name(str(code))


def period_words(days: float) -> tuple[str, str]:
    """A period window in words: ("week", "this week") for 7 days, ("day", "today") for 1,
    ("month", "this month") for 30, else ("14 days", "in these 14 days")."""
    named = {1: ("day", "today"), 7: ("week", "this week"), 30: ("month", "this month")}
    if float(days).is_integer() and int(days) in named:
        return named[int(days)]
    return f"{days:g} days", f"in these {days:g} days"


def _returns_wanted(rule: Rule, target: Any) -> str:
    days = _fmt("", target)
    return f"{days} days or more" if rule.operator == ">=" else f"more than {days} days"


def _limit_words(op: str, cap: str) -> tuple[str, str]:
    """("over", "at CHF 20.00 or less") for ``<=``; ("not under", "under CHF 20.00") for ``<``."""
    return ("over", f"at {cap} or less") if op == "<=" else ("not under", f"under {cap}")


_YES_NO = {  # (field, operator, value): (clause, counterfactual)
    ("cart.recurring", "=", "false"): ("It adds a recurring charge you did not ask for",
                                        "Would approve without the recurring charge"),
    ("order.order_returnable", "=", "true"): ("The order cannot be returned",
                                               "Would approve if the order can be returned"),
    ("order.order_cancellable", "=", "true"): ("The order cannot be cancelled",
                                                "Would approve if the order can be cancelled"),
}  # fmt: skip


def _fail_text(rule: Rule, target: Any, failing: list[tuple[FactValue, ItemFacts | None]],
               facts: Facts) -> tuple[str, str]:  # fmt: skip
    """A broken typed rule as (clause, counterfactual), from its field's template."""
    field, op = rule.field or "", rule.operator or "="
    fv, line = failing[0]
    seen, want = _fmt(field, fv.value), _fmt(field, target)
    if field == "authorization.billing_amount_chf" and op in ("<", "<="):
        broken, within = _limit_words(op, want)
        return f"{seen} is {broken} your {want} limit", f"Would approve {within}"
    if field == "items[].unit_price_chf" and op in ("<", "<="):
        broken, within = _limit_words(op, want)
        item = f"{line.item_name} at {seen}" if line else seen
        return f"{item} is {broken} your {want} item limit", f"Would approve with every item {within}"
    if field in ("items[].size_eu", "items[].size_letter") and op == "=":
        return f"Size {seen}; you asked for {want}", f"Would approve in size {want}"
    if field == "order.return_window_days" and op in (">=", ">"):
        wanted = _returns_wanted(rule, target)
        return f"Returns: {'none' if fv.value == 0 else seen}; you asked for {wanted}", f"Would approve with returns of {wanted}"
    if field == "merchant.merchant_country" and op in ("=", "in", "!=", "not_in"):
        where, listed = _country(fv.value), [_country(v) for v in _values_of(rule)]
        if op in ("=", "in"):
            return f"{facts.merchant_name} is in {where}, not {_either(listed)}", f"Would approve at a shop in {_either(listed)}"
        return f"{facts.merchant_name} is in {where}, which you excluded", f"Would approve at a shop outside {_both(listed)}"
    if field == "authorization.weekday" and op in ("=", "in", "!=", "not_in"):
        day, listed = _day(fv.value), [_day(v) for v in _values_of(rule)]
        if op in ("=", "in"):
            return f"Placed on a {day}, not {_either(listed)}", f"Would approve on {_either(listed)}"
        return f"Placed on a {day}, which you excluded", "Would approve on another day"
    if field in ("items[].quantity", "cart.quantity") and op in ("<", "<="):
        allowed = f"{want} or fewer" if op == "<=" else f"fewer than {want}"
        return f"Quantity {seen}; you allowed {allowed}", f"Would approve with a quantity of {allowed}"
    if (known := _YES_NO.get((field, op, str(rule.value).lower()))) is not None:
        return known
    if field == ALCOHOL_FIELD and op == "=" and str(rule.value).lower() == "false":
        bad = [ln for _, ln in failing if ln is not None]
        return _alcohol_clause(bad), _without(bad)
    label = _LABELS.get(field, "value").capitalize()
    asked = " ".join(part for part in (_OP_WORDS[op], want) if part)
    shown = ", ".join(_fmt(field, v.value) for v, _ in failing)
    return f"{label}: {shown}; you asked for {asked}", f"Would approve with {_want_phrase(rule, target)}"


def _alcohol_clause(lines: list[ItemFacts]) -> str:
    """"Wine and spirits is alcohol, which you excluded"."""
    names = _both(list(dict.fromkeys(ln.item_name for ln in lines)))
    return f"{names} {'is' if len(lines) == 1 else 'are'} alcohol, which you excluded"


def _unknown_text(rule: Rule, target: Any, fv: FactValue, line: ItemFacts | None = None) -> tuple[str, str] | None:
    """A typed rule the shop's text leaves open, when its field has a template: "Returns:
    not stated; you asked for 14 days or more". None keeps the fact's own reason."""
    if rule.field == ALCOHOL_FIELD and line is not None:
        return f"{line.item_name} {fv.detail}", f"Would approve without {line.item_name}"
    if "not stated" not in fv.detail or CONTRADICTORY in fv.detail:
        return None
    if rule.field == "order.return_window_days" and rule.operator in (">=", ">"):
        wanted = _returns_wanted(rule, target)
        return f"Returns: not stated; you asked for {wanted}", f"Would approve with returns of {wanted}"
    if rule.field in ("items[].size_eu", "items[].size_letter") and rule.operator == "=":
        want = _fmt(rule.field, target)
        return f"Size not stated; you asked for {want}", f"Would approve in size {want}"
    return None


# --- Typed rules ---------------------------------------------------------------------
def _asks_when_broken(rule: Rule) -> bool:
    """"...ask me if anything changed": a broken rule is a question, not a decline.
    Needs ``Rule.on_fail`` (P2 contract request); absent, every rule is hard."""
    return getattr(rule, "on_fail", "decline") == "ask"


def _asked_if_broken(rule: Rule, result: RuleResult) -> RuleResult:
    """``on_fail: ask``: a broken rule is unknown, so the customer's uncertainty setting
    applies; its clause and counterfactual stay the field's template."""
    if result.outcome != "fail" or not _asks_when_broken(rule):
        return result
    return result.model_copy(update={"outcome": "unknown", "detail": f"{result.detail}; {ASK_CHANGED}"})


def _shared_check(rule: Rule, facts: Facts) -> RuleResult | None:
    """Typed rules on known shop, item types and shop type are checked by the flag's own
    check, so both paths share the wording and C9's no-history rule (add_ledger_results)."""
    field, op, values = rule.field, rule.operator, _values_of(rule)
    if field in KNOWN_SHOP_FIELDS and op == "=" and str(rule.value).lower() == "true":
        return _check_known_shop(facts, rule.id)
    if field == "items[].item_category" and op == "in":
        return _check_allowed_types(facts, [str(v) for v in values], rule.id)
    if field == "items[].item_category" and op == "not_in":
        return _check_blocked_types(facts, [str(v) for v in values], rule.id)
    if field == "merchant.merchant_category" and op in ("=", "in"):
        return _check_shop_type(facts, [str(v) for v in values], rule.id)
    return None


def evaluate_typed_rule(rule: Rule, facts: Facts, policy: Policy) -> RuleResult:
    """Check one typed rule. Every value must satisfy it (every cart line for item fields).
    Any fail -> fail. Otherwise any unknown -> unknown. Otherwise pass."""
    label = _LABELS.get(rule.field or "", rule.text or "this restriction")
    if not rule.field or rule.operator is None or rule.value is None:
        return RuleResult(rule_id=rule.id, outcome="unknown", source="event",
                          detail=f'No data to check "{rule.text or rule.id}"')
    if (shared := _shared_check(rule, facts)) is not None:
        return _asked_if_broken(rule, shared)
    looked_up = _facts_for(rule.field, facts, policy)
    if looked_up is None:
        return RuleResult(rule_id=rule.id, outcome="unknown", source="event",
                          detail=f'No data to check "{rule.text or rule.field}"')
    values, lines = looked_up
    target = _rule_value_chf(rule)

    failing, unknown = [], []
    for fv, line in zip(values, lines, strict=True):
        if not fv.known:
            unknown.append((fv, line))
        elif not _compare(fv.value, rule.operator, target):
            failing.append((fv, line))

    source = next((fv.source for fv in [f for f, _ in failing + unknown] or values), "event")
    if failing:
        clause, counterfactual = _fail_text(rule, target, failing, facts)
        return _asked_if_broken(rule, RuleResult(rule_id=rule.id, outcome="fail", source=source,
                                                 detail=clause, counterfactual=counterfactual))
    if unknown:
        templated = _unknown_text(rule, target, *unknown[0])
        if templated is not None:
            return RuleResult(rule_id=rule.id, outcome="unknown", source=source,
                              detail=templated[0], counterfactual=templated[1])
        reason = unknown[0][0].detail or "not stated"
        flag = " (the shop contradicts itself)" if reason.startswith(CONTRADICTORY) else ""
        return RuleResult(rule_id=rule.id, outcome="unknown", source=source,
                          detail=f"{label.capitalize()} unknown{flag}: {reason}")
    if rule.field == ALCOHOL_FIELD:
        return RuleResult(rule_id=rule.id, outcome="pass", source=source, detail="No alcohol in the cart")
    seen = ", ".join(sorted({_fmt(rule.field, fv.value) for fv in values}))
    return RuleResult(rule_id=rule.id, outcome="pass", source=source,
                      detail=f"{label.capitalize()}: {seen}. Meets {_want_phrase(rule, target)}")


def _want_phrase(rule: Rule, target: Any) -> str:
    """e.g. "order total at or below CHF 120.00", "size 43", "return window at least 14 days"."""
    label = _LABELS.get(rule.field or "", "value")
    op = _OP_WORDS[rule.operator or "="]
    return " ".join(part for part in (label, op, _fmt(rule.field or "", target)) if part)


# --- Structured flags ----------------------------------------------------------------
def _covered(policy: Policy, field: str, ops: Iterable[str]) -> bool:
    return any(r.field == field and r.operator in ops for r in policy.rules)


def _category_clause(bad: list[ItemFacts], tail: str) -> str:
    """"Face cream is cosmetics, not groceries"; "A and B are cosmetics and toys, …"."""
    names = _both([ln.item_name for ln in bad])
    kinds = _both(list(dict.fromkeys(_human(ln.item_category) for ln in bad)))
    return f"{names} {'is' if len(bad) == 1 else 'are'} {kinds}, {tail}"


def _without(bad: list[ItemFacts]) -> str:
    return f"Would approve without {_both(list(dict.fromkeys(ln.item_name for ln in bad)))}"


def _check_allowed_types(facts: Facts, allowed: list[str], rule_id: str = "C3") -> RuleResult:
    wanted = {c.lower() for c in allowed}
    bad = [ln for ln in facts.items if ln.item_category.lower() not in wanted]
    words = [_human(c) for c in allowed]
    if bad:
        return RuleResult(rule_id=rule_id, outcome="fail", source="event",
                          detail=_category_clause(bad, f"not {_either(words)}"), counterfactual=_without(bad))
    return RuleResult(rule_id=rule_id, outcome="pass", source="event", detail=f"Every item is {_either(words)}")


def _check_blocked_types(facts: Facts, blocked: list[str], rule_id: str = "C4") -> RuleResult:
    excluded = {c.lower() for c in blocked}
    bad = [ln for ln in facts.items if ln.item_category.lower() in excluded]
    if bad:
        return RuleResult(rule_id=rule_id, outcome="fail", source="event",
                          detail=_category_clause(bad, "which you excluded"), counterfactual=_without(bad))
    return RuleResult(rule_id=rule_id, outcome="pass", source="event",
                      detail=f"No excluded items ({', '.join(_human(c) for c in blocked)})")


def _check_requested_item(facts: Facts, policy: Policy) -> RuleResult:
    wanted = policy.requested_item or ""
    if _requested_lines(facts, policy):
        return RuleResult(rule_id="C5", outcome="pass", source="event", detail=f"Cart contains the {wanted}")
    names = _both([ln.item_name for ln in facts.items])
    return RuleResult(rule_id="C5", outcome="fail", source="event",
                      detail=f"The cart has {names}, not the {wanted} you asked for",
                      counterfactual=f"Would approve with the {wanted}")


def _check_shop_type(facts: Facts, wanted: list[str], rule_id: str = "C8") -> RuleResult:
    shop = f"{_either([_human(w) for w in wanted])} shop"
    if facts.merchant_category is None:
        return RuleResult(rule_id=rule_id, outcome="unknown", source="event",
                          detail=f"Couldn't check whether {facts.merchant_name} is {_a(shop)}")
    if facts.merchant_category.lower() in {w.lower() for w in wanted}:
        return RuleResult(rule_id=rule_id, outcome="pass", source="event",
                          detail=f"{facts.merchant_name} is {_a(_human(facts.merchant_category))} shop")
    return RuleResult(rule_id=rule_id, outcome="fail", source="event",
                      detail=f"{facts.merchant_name} is {_a(_human(facts.merchant_category))} shop, not {_a(shop)}",
                      counterfactual=f"Would approve at {_a(shop)}")


def _check_known_shop(facts: Facts, rule_id: str = "C9") -> RuleResult:
    if facts.merchant_known is None:
        return RuleResult(rule_id=rule_id, outcome="unknown", source="history",
                          detail=f"Couldn't check whether you've bought from {facts.merchant_name} before")
    if facts.merchant_known:
        return RuleResult(rule_id=rule_id, outcome="pass", source="history",
                          detail=f"You've bought from {facts.merchant_name} before")
    return RuleResult(rule_id=rule_id, outcome="fail", source="history",
                      detail=f"You haven't bought from {facts.merchant_name} before",
                      counterfactual="Would approve at a shop you've bought from before")


def _check_nothing_extra(facts: Facts, policy: Policy) -> RuleResult:
    if policy.requested_item:
        extra = [ln for ln in facts.items if not matches_requested_item(ln, policy.requested_item)]
    else:
        extra = [ln for ln in facts.items
                 if policy.allowed_item_categories and ln.item_category not in policy.allowed_item_categories]
    if extra:
        names = _both([ln.item_name for ln in extra])
        return RuleResult(rule_id="C10", outcome="fail", source="event",
                          detail=f"The cart adds {names}, which you didn't ask for",
                          counterfactual=f"Would approve without {names}")
    return RuleResult(rule_id="C10", outcome="pass", source="event", detail="Nothing extra in the cart")

# --- Public --------------------------------------------------------------------------
def step1_results(facts: Facts, policy: Policy) -> list[RuleResult]:
    """rules.md §4 step 1: policy active, authority active, card not blocked.
    A fail on any of these is decided first by decide.py (types.STEP1_RULE_IDS)."""
    checks = {
        "policy_status": (policy.status == "active", f"The policy is {policy.status}",
                          "Would approve under an active policy"),
        "authority_status": (facts.authority_status == "active", f"The agent's authority is {facts.authority_status}",
                             "Would approve with an active authority"),
        "card_status": (facts.card_status_at_attempt == "active", f"The card is {facts.card_status_at_attempt}",
                        "Would approve on an active card"),
    }
    out = []
    for rule_id in STEP1_RULE_IDS:
        ok, detail, counterfactual = checks[rule_id]
        out.append(RuleResult(rule_id=rule_id, outcome="pass" if ok else "fail", source="event",
                              detail=detail, counterfactual=None if ok else counterfactual))
    return out


@register("evaluate_rules")
def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]:
    """Step-1 checks, then C1, C3-C10, C12 for one purchase. Period limits (C2) are in
    evaluate_period_rule. C11 (uncertainty setting) is applied by decide.py."""
    results = step1_results(facts, policy)
    results += [evaluate_typed_rule(r, facts, policy) for r in policy.rules if r.scope != "period"]

    if policy.allowed_item_categories and not _covered(policy, "items[].item_category", ("in",)):
        results.append(_check_allowed_types(facts, policy.allowed_item_categories))
    if policy.blocked_item_categories and not _covered(policy, "items[].item_category", ("not_in",)):
        results.append(_check_blocked_types(facts, policy.blocked_item_categories))
    if policy.requested_item:
        results.append(_check_requested_item(facts, policy))
    if policy.shop_type and not _covered(policy, "merchant.merchant_category", ("=", "in")):
        results.append(_check_shop_type(facts, [policy.shop_type]))
    if policy.requires_known_shop and not (_covered(policy, "merchant.familiar_on_card", ("=",))
                                           or _covered(policy, "merchant.known_shop", ("=",))):
        results.append(_check_known_shop(facts))
    if policy.nothing_extra:
        results.append(_check_nothing_extra(facts, policy))
    return results


def period_rules(policy: Policy) -> list[Rule]:
    return [r for r in policy.rules if r.scope == "period"]


def evaluate_period_rule(rule: Rule, facts: Facts, spent_chf: Any, reserved_chf: Any) -> RuleResult:
    """C2 for one period rule (M4, M5).

    ``spent_chf``: final approvals in this rule's window before this purchase.
    ``reserved_chf``: pending (reserved) amounts in the same window.
    Fails if spent + reserved + this purchase breaks the limit. If it would pass on final
    approvals alone, the detail carries RESERVATION_ONLY so decide.py can ask instead (M5).
    """
    limit = Decimal(str(_rule_value_chf(rule)))
    spent, reserved = Decimal(str(spent_chf)), Decimal(str(reserved_chf))
    this = Decimal(str(facts.billing_amount_chf))
    days = rule.period_days or 7
    period, this_period = period_words(days)
    with_reserved, finals_only = spent + reserved + this, spent + this
    ok = (lambda total: _compare(total, rule.operator or "<=", limit))
    if ok(with_reserved):
        return RuleResult(rule_id=rule.id, outcome="pass", source="history",
                          detail=f"{days:g}-day total {_money(with_reserved)} of your {_money(limit)} limit")
    over = f"this would take the {period} to {_money(with_reserved)}, over your {_money(limit)}"
    if ok(finals_only):
        waiting = _money(reserved)
        return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                          detail=f"With {waiting} unanswered, {over}; {RESERVATION_ONLY}",
                          counterfactual=f"Would approve if you decline the unanswered {waiting}")
    room = limit - spent - reserved
    counterfactual = (f"Would approve at {_money(room)} or less {this_period}"
                      if room > 0 else f"Nothing more fits {this_period}")
    return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                      detail=over[0].upper() + over[1:], counterfactual=counterfactual)


_COUNT_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _count(n: int) -> str:
    return _COUNT_WORDS[n] if 0 <= n < len(_COUNT_WORDS) else str(n)


def _orders(n: int) -> str:
    return f"{_count(n)} order" + ("" if n == 1 else "s")


def _per_period(days: int) -> str:
    period, _ = period_words(days)
    return f"per {period}" if " " not in period else f"in any {period}"


def _when(at: datetime, now: datetime) -> str:
    """Simulated time in Europe/Zurich, relative to this purchase: "today at 12:10"."""
    local, today = at.astimezone(ZURICH), now.astimezone(ZURICH).date()
    day = {0: "today", -1: "yesterday", 1: "tomorrow"}.get((local.date() - today).days)
    return f"{day or f'on {local:%a} {local.day} {local:%b}'} at {local:%H:%M}"


def _allowed_count(rule: Rule) -> int | None:
    """How many purchases the rule allows in its window, or None if it is not a cap."""
    value = rule.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or rule.operator not in ("<", "<="):
        return None
    return max(value if rule.operator == "<=" else value - 1, 0)


def _not_counted(rule: Rule) -> RuleResult:
    return RuleResult(rule_id=rule.id, outcome="unknown", source="history",
                      detail=f'Orders for "{rule.text or rule.id}" could not be counted')


def evaluate_count_rule(rule: Rule, facts: Facts, ledger: LedgerView) -> RuleResult:
    """A purchase count per period (``cart.purchases_in_period``, M4, M5).

    ``ledger.period_count``: this card's final approvals plus pending step-ups in the
    rule's window; this purchase makes one more. Fails if that breaks the limit. If it
    would pass on final approvals alone, the detail carries RESERVATION_ONLY so decide.py
    asks instead (M5). Not counted (``None``) is unknown, never a pass (P3).
    """
    allowed = _allowed_count(rule)
    if allowed is None or ledger.period_count is None or not rule.period_days:
        return _not_counted(rule)
    days = rule.period_days
    counted, waiting = ledger.period_count, ledger.period_reserved_count
    approved = counted - waiting
    stated = f"You allowed {_orders(allowed)} {_per_period(days)}"
    if counted + 1 <= allowed:
        return RuleResult(rule_id=rule.id, outcome="pass", source="history",
                          detail=f"{stated}; this is order {_count(counted + 1)}")
    pending = f"{_count(waiting)} {'is' if waiting == 1 else 'are'} still unanswered"
    if approved + 1 <= allowed:
        orders = "unanswered order" if waiting == 1 else f"{_count(waiting)} unanswered orders"
        return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                          detail=f"{stated}; {pending}; {RESERVATION_ONLY}",
                          counterfactual=f"Would approve if you decline the {orders}")
    last = ledger.period_last_approved_at
    when = f" {_when(last, facts.timestamp)}" if last is not None else ""
    # One clause: the time of the approval only when it is the one order in the way.
    if approved == 1 and not waiting:
        done = f"one was already approved{when}"
    else:
        done = f"{_count(approved)} {'was' if approved == 1 else 'were'} already approved"
        done += f" and {pending}" if waiting else ""
    if not allowed:
        counterfactual = None
    elif approved == 1 and not waiting and last is not None:
        counterfactual = f"Would approve from {_when(last + timedelta(days=days), facts.timestamp)}"
    else:
        counterfactual = f"Would approve once fewer than {_orders(allowed + 1)} fall in the last {period_words(days)[0]}"
    return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                      detail=f"{stated}; {done}", counterfactual=counterfactual)


def is_unverifiable(rule: Rule, facts: Facts, policy: Policy) -> bool:
    """A restriction no data can check ("official ticket seller"): no field, or a field
    outside the vocabulary. Only these can be passed from a remembered answer."""
    return not rule.field or _facts_for(rule.field, facts, policy) is None


def checks_known_shop(result: RuleResult, rule: Rule | None) -> bool:
    """C9: the ``requires_known_shop`` flag's result, or a typed rule on a known-shop field."""
    return rule.field in KNOWN_SHOP_FIELDS if rule is not None else result.rule_id == "C9"


def _known_shop_with_ledger(res: RuleResult, facts: Facts, ledger: LedgerView, confirmed: set[str]) -> RuleResult:
    """C9 with the ledger. No purchase history yet (no approved purchase in history or in
    this run, on any card) -> unknown, not a fail: the uncertainty setting decides. An
    unknown passes when the customer already approved this rule at this shop, whatever
    the items (ask once, then remember the shop)."""
    if res.outcome == "pass":
        return res
    if not ledger.known_merchant_ids:
        res = RuleResult(rule_id=res.rule_id, outcome="unknown", source="history", detail=NO_HISTORY,
                         counterfactual=NO_HISTORY_COUNTERFACTUAL)
    if res.outcome == "unknown" and shop_confirmation_key(res.rule_id, facts.merchant_id) in confirmed:
        return RuleResult(rule_id=res.rule_id, outcome="pass", source="history", detail=f"{CONFIRMED} shop earlier")
    return res


def add_ledger_results(
    rules: list[RuleResult], facts: Facts, policy: Policy, ledger: LedgerView
) -> list[RuleResult]:
    """The rule results that need the ledger, added to ``evaluate_rules``' output.

    - C2: one result per period rule, from the LedgerView's spent and reserved amounts
      (M4, M5), or from its purchase count for ``cart.purchases_in_period``. The view
      covers one window (the shortest period); a period rule with a different window is
      unknown rather than checked against the wrong numbers (P3).
    - C9: unknown with no purchase history yet; remembered per shop (``_known_shop_with_ledger``).
    - Ask once, then remember: an unknown restriction no data can check passes when the
      customer already approved it for this shop and every item in the cart.
    """
    by_id = {r.id: r for r in policy.rules}
    confirmed: set[str] = set(getattr(ledger, "confirmed_keys", set()) or set())
    out: list[RuleResult] = []
    for res in rules:
        rule = by_id.get(res.rule_id)
        if checks_known_shop(res, rule):
            res = _known_shop_with_ledger(res, facts, ledger, confirmed)
        elif (res.outcome == "unknown" and rule is not None and confirmed
                and is_unverifiable(rule, facts, policy)
                and all(confirmation_key(rule.id, facts.merchant_id, ln.item_id) in confirmed
                        for ln in facts.items)):
            res = RuleResult(rule_id=res.rule_id, outcome="pass", source="history",
                             detail=f'{CONFIRMED} for this shop and item earlier: "{rule.text or rule.id}"')
        out.append(res)

    view_days = (facts.timestamp - ledger.period_window_start).total_seconds() / 86400
    for rule in period_rules(policy):
        in_view = bool(rule.period_days) and abs(rule.period_days - view_days) < 1e-9
        if rule.field == COUNT_FIELD:
            out.append(evaluate_count_rule(rule, facts, ledger) if in_view else _not_counted(rule))
        elif in_view:
            out.append(evaluate_period_rule(rule, facts, ledger.period_spent_chf, ledger.period_reserved_chf))
        else:
            out.append(RuleResult(rule_id=rule.id, outcome="unknown", source="history",
                                  detail=f'Spending for "{rule.text or rule.id}" could not be totalled'))
    return out
