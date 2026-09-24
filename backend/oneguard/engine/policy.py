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

Period limits (C2, ``scope == "period"``) and remembered confirmations need the ledger,
which ``evaluate_rules`` does not receive. ``add_ledger_results(rules, facts, policy,
ledger)`` adds them: the pipeline calls it after ``evaluate_rules`` (and after tier 2), so
decide.py and explain.py both see them (P2 request to P1: one line in pipeline.py). Until
then decide.py treats a period rule with no result as unknown (P3).

Pure function: no I/O, no CSV, no history lookups. Familiarity (C9) is read from
``facts.merchant_known``, set by the pipeline from the LedgerView.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from oneguard.engine.facts import CONTRADICTORY, to_chf
from oneguard.engine.interfaces import register
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
def _facts_for(field: str, facts: Facts, policy: Policy) -> tuple[list[FactValue], str] | None:
    """Return (values to check, what they describe) or None if the field is not known."""
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
        return [single[field]], "order"
    per_line = {
        "items[].item_category": lambda ln: _fv(ln.item_category),
        "items[].unit_price_chf": lambda ln: _fv(ln.unit_price_chf),
        "items[].quantity": lambda ln: _fv(ln.quantity),
        "items[].size_eu": lambda ln: ln.size_eu,
        "items[].size_letter": lambda ln: getattr(ln, "size_letter", None) or FactValue(
            known=False, source="regex", detail="letter sizes are not in the contract yet"),
    }
    if field in per_line:
        chosen = product_lines if field in _PRODUCT_FIELDS else lines
        if not chosen:
            return [FactValue(known=False, source="event", detail="not checked, the cart does not contain the requested item")], "item"
        return [per_line[field](ln) for ln in chosen], "item"
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


# --- Typed rules ---------------------------------------------------------------------
def _asks_when_broken(rule: Rule) -> bool:
    """"...ask me if anything changed": a broken rule is a question, not a decline.
    Needs ``Rule.on_fail`` (P2 contract request); absent, every rule is hard."""
    return getattr(rule, "on_fail", "decline") == "ask"



def evaluate_typed_rule(rule: Rule, facts: Facts, policy: Policy) -> RuleResult:
    """Check one typed rule. Every value must satisfy it (every cart line for item fields).
    Any fail -> fail. Otherwise any unknown -> unknown. Otherwise pass."""
    label = _LABELS.get(rule.field or "", rule.text or "this restriction")
    if not rule.field or rule.operator is None or rule.value is None:
        return RuleResult(rule_id=rule.id, outcome="unknown", source="event",
                          detail=f'No data to check "{rule.text or rule.id}"')
    looked_up = _facts_for(rule.field, facts, policy)
    if looked_up is None:
        return RuleResult(rule_id=rule.id, outcome="unknown", source="event",
                          detail=f'No data to check "{rule.text or rule.field}"')
    values, _ = looked_up
    target = _rule_value_chf(rule)
    want = _want_phrase(rule, target)

    failing, unknown = [], []
    for fv in values:
        if not fv.known:
            unknown.append(fv)
        elif not _compare(fv.value, rule.operator, target):
            failing.append(fv)

    source = next((fv.source for fv in failing or unknown or values), "event")
    if failing:
        seen = ", ".join(_fmt(rule.field, fv.value) for fv in failing)
        if _asks_when_broken(rule):
            # "…ask me if anything changed": a broken rule is a question, not a decline.
            return RuleResult(rule_id=rule.id, outcome="unknown", source=source,
                              detail=f"{label.capitalize()} changed: {seen}. You asked for {want} "
                                     "and to be asked if anything changed",
                              counterfactual=f"Would approve with {want}")
        return RuleResult(rule_id=rule.id, outcome="fail", source=source,
                          detail=f"{label.capitalize()}: {seen}. You asked for {want}",
                          counterfactual=f"Would approve with {want}")
    if unknown:
        reason = unknown[0].detail or "not stated"
        flag = " (the shop contradicts itself)" if reason.startswith(CONTRADICTORY) else ""
        return RuleResult(rule_id=rule.id, outcome="unknown", source=source,
                          detail=f"{label.capitalize()} unknown{flag}: {reason}")
    seen = ", ".join(sorted({_fmt(rule.field, fv.value) for fv in values}))
    return RuleResult(rule_id=rule.id, outcome="pass", source=source,
                      detail=f"{label.capitalize()}: {seen}. Meets {want}")


def _want_phrase(rule: Rule, target: Any) -> str:
    """e.g. "order total at or below CHF 120.00", "size 43", "return window at least 14 days"."""
    label = _LABELS.get(rule.field or "", "value")
    op = _OP_WORDS[rule.operator or "="]
    return " ".join(part for part in (label, op, _fmt(rule.field or "", target)) if part)


# --- Structured flags ----------------------------------------------------------------
def _covered(policy: Policy, field: str, ops: Iterable[str]) -> bool:
    return any(r.field == field and r.operator in ops for r in policy.rules)


def _check_allowed_types(facts: Facts, policy: Policy) -> RuleResult:
    bad = [ln for ln in facts.items if ln.item_category not in policy.allowed_item_categories]
    allowed = ", ".join(_human(c) for c in policy.allowed_item_categories)
    if bad:
        names = ", ".join(f"{ln.item_name} ({_human(ln.item_category)})" for ln in bad)
        return RuleResult(rule_id="C3", outcome="fail", source="event",
                          detail=f"Cart includes {names}; you allowed only {allowed}",
                          counterfactual=f"Would approve without {', '.join(ln.item_name for ln in bad)}")
    return RuleResult(rule_id="C3", outcome="pass", source="event", detail=f"Every item is {allowed}")


def _check_blocked_types(facts: Facts, policy: Policy) -> RuleResult:
    bad = [ln for ln in facts.items if ln.item_category in policy.blocked_item_categories]
    if bad:
        names = ", ".join(f"{ln.item_name} ({_human(ln.item_category)})" for ln in bad)
        return RuleResult(rule_id="C4", outcome="fail", source="event",
                          detail=f"Cart includes {names}, which you excluded",
                          counterfactual=f"Would approve without {', '.join(ln.item_name for ln in bad)}")
    return RuleResult(rule_id="C4", outcome="pass", source="event",
                      detail=f"No excluded items ({', '.join(_human(c) for c in policy.blocked_item_categories)})")


def _check_requested_item(facts: Facts, policy: Policy) -> RuleResult:
    wanted = policy.requested_item or ""
    if _requested_lines(facts, policy):
        return RuleResult(rule_id="C5", outcome="pass", source="event", detail=f"Cart contains the {wanted}")
    names = ", ".join(ln.item_name for ln in facts.items)
    return RuleResult(rule_id="C5", outcome="fail", source="event",
                      detail=f"Cart contains {names}, not the {wanted} you asked for",
                      counterfactual=f"Would approve with the {wanted}")


def _check_shop_type(facts: Facts, policy: Policy) -> RuleResult:
    want = policy.shop_type or ""
    if facts.merchant_category == want:
        return RuleResult(rule_id="C8", outcome="pass", source="event", detail=f"{facts.merchant_name} is a {_human(want)} shop")
    return RuleResult(rule_id="C8", outcome="fail", source="event",
                      detail=f"{facts.merchant_name} is a {_human(facts.merchant_category)} shop, not {_human(want)}",
                      counterfactual=f"Would approve at a {_human(want)} shop")


def _check_known_shop(facts: Facts) -> RuleResult:
    if facts.merchant_known is None:
        return RuleResult(rule_id="C9", outcome="unknown", source="history",
                          detail=f"Couldn't check whether you've bought from {facts.merchant_name} before")
    if facts.merchant_known:
        return RuleResult(rule_id="C9", outcome="pass", source="history",
                          detail=f"You've bought from {facts.merchant_name} before")
    return RuleResult(rule_id="C9", outcome="fail", source="history",
                      detail=f"You haven't bought from {facts.merchant_name} before",
                      counterfactual="Would approve at a shop you've bought from before")


def _check_nothing_extra(facts: Facts, policy: Policy) -> RuleResult:
    if policy.requested_item:
        extra = [ln for ln in facts.items if not matches_requested_item(ln, policy.requested_item)]
    else:
        extra = [ln for ln in facts.items
                 if policy.allowed_item_categories and ln.item_category not in policy.allowed_item_categories]
    if extra:
        names = ", ".join(ln.item_name for ln in extra)
        return RuleResult(rule_id="C10", outcome="fail", source="event",
                          detail=f"Cart includes {names}, which you didn't ask for",
                          counterfactual=f"Would approve without {names}")
    return RuleResult(rule_id="C10", outcome="pass", source="event", detail="Nothing extra in the cart")


# --- Public --------------------------------------------------------------------------
def step1_results(facts: Facts, policy: Policy) -> list[RuleResult]:
    """rules.md §4 step 1: policy active, authority active, card not blocked.
    A fail on any of these is decided first by decide.py (types.STEP1_RULE_IDS)."""
    checks = {
        "policy_status": (policy.status == "active", f"policy is {policy.status}",
                          "Would approve under an active policy"),
        "authority_status": (facts.authority_status == "active", f"authority is {facts.authority_status}",
                             "Would approve with an active authority"),
        "card_status": (facts.card_status_at_attempt == "active", f"card is {facts.card_status_at_attempt}",
                        "Would approve on an active card"),
    }
    out = []
    for rule_id in STEP1_RULE_IDS:
        ok, detail, counterfactual = checks[rule_id]
        out.append(RuleResult(rule_id=rule_id, outcome="pass" if ok else "fail", source="event",
                              detail=detail.capitalize(), counterfactual=None if ok else counterfactual))
    return out


@register("evaluate_rules")
def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]:
    """Step-1 checks, then C1, C3-C10, C12 for one purchase. Period limits (C2) are in
    evaluate_period_rule. C11 (uncertainty setting) is applied by decide.py."""
    results = step1_results(facts, policy)
    results += [evaluate_typed_rule(r, facts, policy) for r in policy.rules if r.scope != "period"]

    if policy.allowed_item_categories and not _covered(policy, "items[].item_category", ("in",)):
        results.append(_check_allowed_types(facts, policy))
    if policy.blocked_item_categories and not _covered(policy, "items[].item_category", ("not_in",)):
        results.append(_check_blocked_types(facts, policy))
    if policy.requested_item:
        results.append(_check_requested_item(facts, policy))
    if policy.shop_type and not _covered(policy, "merchant.merchant_category", ("=", "in")):
        results.append(_check_shop_type(facts, policy))
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
    window = f"{days}-day"
    with_reserved, finals_only = spent + reserved + this, spent + this
    ok = (lambda total: _compare(total, rule.operator or "<=", limit))
    if ok(with_reserved):
        return RuleResult(rule_id=rule.id, outcome="pass", source="history",
                          detail=f"{window} total {_money(with_reserved)} of your {_money(limit)} limit")
    if ok(finals_only):
        waiting = _money(reserved)
        return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                          detail=(f"{window} total would be {_money(with_reserved)} including {waiting} still "
                                  f"waiting for your answer; {RESERVATION_ONLY}"),
                          counterfactual=f"Would approve if you decline the {waiting} order still waiting")
    headroom = limit - spent - reserved
    counterfactual = (f"Would approve at {_money(headroom)} or less in this {window} window"
                      if headroom > 0 else f"nothing more fits in this {window} window")
    return RuleResult(rule_id=rule.id, outcome="fail", source="history",
                      detail=f"{window} total would be {_money(with_reserved)}, over your {_money(limit)} limit",
                      counterfactual=counterfactual)


def is_unverifiable(rule: Rule, facts: Facts, policy: Policy) -> bool:
    """A restriction no data can check ("official ticket seller"): no field, or a field
    outside the vocabulary. Only these can be passed from a remembered answer."""
    return not rule.field or _facts_for(rule.field, facts, policy) is None


def _confirmation_key(rule_id: str, merchant_id: str, item_id: str) -> str:
    return f"{rule_id}|{merchant_id}|{item_id}"  # same format as ledger.confirmation_key


def add_ledger_results(
    rules: list[RuleResult], facts: Facts, policy: Policy, ledger: LedgerView
) -> list[RuleResult]:
    """The rule results that need the ledger, added to ``evaluate_rules``' output.

    - C2: one result per period rule, from the LedgerView's spent and reserved amounts
      (M4, M5). The view covers one window (the shortest period); a period rule with a
      different window is unknown rather than checked against the wrong numbers (P3).
    - Ask once, then remember: an unknown restriction no data can check passes when the
      customer already approved it for this shop and every item in the cart.
    """
    by_id = {r.id: r for r in policy.rules}
    confirmed: set[str] = set(getattr(ledger, "confirmed_keys", set()) or set())
    out: list[RuleResult] = []
    for res in rules:
        rule = by_id.get(res.rule_id)
        if (res.outcome == "unknown" and rule is not None and confirmed
                and is_unverifiable(rule, facts, policy)
                and all(_confirmation_key(rule.id, facts.merchant_id, ln.item_id) in confirmed
                        for ln in facts.items)):
            res = RuleResult(rule_id=res.rule_id, outcome="pass", source="history",
                             detail=f'{CONFIRMED} for this shop and item earlier: "{rule.text or rule.id}"')
        out.append(res)

    view_days = (facts.timestamp - ledger.period_window_start).total_seconds() / 86400
    for rule in period_rules(policy):
        if rule.period_days and abs(rule.period_days - view_days) < 1e-9:
            out.append(evaluate_period_rule(rule, facts, ledger.period_spent_chf, ledger.period_reserved_chf))
        else:
            out.append(RuleResult(rule_id=rule.id, outcome="unknown", source="history",
                                  detail=f'Spending for "{rule.text or rule.id}" could not be totalled'))
    return out
