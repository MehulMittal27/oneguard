"""Tests for engine/policy.py (lane P2). Synthetic inputs only; no scenario or purchase ids."""
from __future__ import annotations

import copy

import pytest

from oneguard.engine.facts import _HAS_SIZE_LETTER, _SIZE_EU_DECIMAL, build_facts
from oneguard.engine.policy import (
    RESERVATION_ONLY,
    evaluate_period_rule,
    evaluate_rules,
    matches_requested_item,
    period_rules,
)
from oneguard.engine.types import STEP1_RULE_IDS, Policy, Rule

BASE = {
    "authorization": {
        "authorization_id": "LIVE_1",
        "merchant": {"merchant_id": "M1", "merchant_name": "Test Shop", "merchant_category": "sporting_goods",
                     "merchant_country": "CH"},
        "timestamp": "2026-08-12T10:00:00Z",  # Wednesday 12:00 Zurich
        "amount": 120.0, "currency": "CHF", "billing_amount_chf": 120.0,
        "customer_device_id": "D1", "recent_attempt_count_10m": 0, "delivery_by": None,
        "order_returnable": "true", "order_cancellable": "unknown",
        "related_authorization_id": None, "related_authorization_status": None,
        "items": [{"line_no": 1, "item_id": "I1", "item_name": "Road-running shoes", "item_category": "sporting_goods",
                   "quantity": 1, "unit_price": 120.0, "currency": "CHF",
                   "item_details": "Road-running shoe, size 43; returns accepted within 30 days"}],
    }
}


def facts(known: bool = True, lines=None, **auth):
    e = copy.deepcopy(BASE)
    e["authorization"].update(auth)
    if lines is not None:
        e["authorization"]["items"] = [
            {"line_no": i + 1, "item_id": f"I{i + 1}", "quantity": 1, "currency": "CHF", "unit_price": 10.0, **ln}
            for i, ln in enumerate(lines)
        ]
    f = build_facts(e)
    f.merchant_known = known
    return f


def policy(**kw) -> Policy:
    kw.setdefault("rules", [])
    kw.setdefault("status", "active")
    kw.setdefault("uncertainty_policy", "ask")
    return Policy(mandate_id="T1", instruction="test", **kw)


def rule(id_, field, op, value, **kw) -> Rule:
    kw.setdefault("text", f"{field} {op} {value}")
    kw.setdefault("source", "exact")
    return Rule(id=id_, field=field, operator=op, value=value, **kw)


def customer_results(results):
    """Drop the three step-1 checks (policy / authority / card status)."""
    return [r for r in results if r.rule_id not in STEP1_RULE_IDS]


def only(results, rule_id):
    found = [r for r in results if r.rule_id == rule_id]
    assert len(found) == 1, f"expected one {rule_id}, got {found}"
    return found[0]


ORDER = "authorization.billing_amount_chf"


# --- Rules apply only when stated ------------------------------------------------------
def test_empty_policy_checks_only_step1():
    results = evaluate_rules(facts(), policy())
    assert [r.rule_id for r in results] == list(STEP1_RULE_IDS)
    assert all(r.outcome == "pass" for r in results)


@pytest.mark.parametrize("status_field,value,rule_id", [
    ("policy", "revoked", "policy_status"),
    ("policy", "expired", "policy_status"),
    ("auth", {"authority_status": "revoked"}, "authority_status"),
    ("auth", {"card_status_at_attempt": "blocked"}, "card_status"),
])
def test_step1_fails_are_reported(status_field, value, rule_id):
    if status_field == "policy":
        results = evaluate_rules(facts(), policy(status=value))
    else:
        results = evaluate_rules(facts(**value), policy())
    r = only(results, rule_id)
    assert r.outcome == "fail" and r.counterfactual


# --- C1 order limit: boundary wording -------------------------------------------------
@pytest.mark.parametrize("op,limit,outcome", [
    ("<=", 120, "pass"),   # "at or below / no more than / max / up to": equal passes
    ("<", 120, "fail"),    # "under / less than / below": equal fails
    ("<=", 119.99, "fail"),
    ("<", 120.01, "pass"),
])
def test_c1_boundary(op, limit, outcome):
    r = only(evaluate_rules(facts(), policy(rules=[rule("C1", ORDER, op, limit, scope="purchase")])), "C1")
    assert r.outcome == outcome


def test_c1_fail_names_numbers_and_counterfactual():
    f = facts(amount=126.0, billing_amount_chf=126.0)
    r = only(evaluate_rules(f, policy(rules=[rule("C1", ORDER, "<=", 120, scope="purchase")])), "C1")
    assert r.outcome == "fail"
    assert "CHF 126.00" in r.detail and "CHF 120.00" in r.detail
    assert r.counterfactual == "Would approve at CHF 120.00 or less"


def test_c1_limit_in_foreign_currency_is_converted():
    # "max EUR 30" = CHF 28.50
    r = lambda amt: only(evaluate_rules(facts(amount=amt, billing_amount_chf=amt),
                                        policy(rules=[rule("C1", ORDER, "<=", 30, currency="EUR")])), "C1").outcome
    assert r(28.50) == "pass"
    assert r(28.51) == "fail"


def test_rule_without_field_is_unknown_not_pass():
    x = rule("X", "unverifiable", "=", "from the official ticket seller", text="from the official ticket seller")
    r = only(evaluate_rules(facts(), policy(rules=[x])), "X")
    assert r.outcome == "unknown" and "official ticket seller" in r.detail


def test_rule_with_unmapped_field_is_unknown():
    r = only(evaluate_rules(facts(), policy(rules=[rule("X", "merchant.is_official", "=", "true")])), "X")
    assert r.outcome == "unknown"


# --- C3 / C4 item types: every cart line -------------------------------------------------
def test_c3_every_line_must_be_allowed():
    f = facts(lines=[{"item_name": "Fruit", "item_category": "groceries", "item_details": ""},
                     {"item_name": "Perfume gift set", "item_category": "cosmetics", "item_details": ""}])
    r = only(evaluate_rules(f, policy(allowed_item_categories=["groceries"])), "C3")
    assert r.outcome == "fail" and "Perfume gift set" in r.detail
    assert r.counterfactual == "Would approve without Perfume gift set"


def test_c3_shop_category_does_not_prove_basket():
    f = facts(lines=[{"item_name": "Lipstick", "item_category": "cosmetics", "item_details": ""}])
    f.merchant_category = "groceries"
    assert only(evaluate_rules(f, policy(allowed_item_categories=["groceries"])), "C3").outcome == "fail"


def test_c4_blocked_types():
    f = facts(lines=[{"item_name": "Voucher", "item_category": "gift_card", "item_details": ""}])
    assert only(evaluate_rules(f, policy(blocked_item_categories=["gift_card"])), "C4").outcome == "fail"
    assert only(evaluate_rules(facts(), policy(blocked_item_categories=["gift_card"])), "C4").outcome == "pass"


def test_typed_category_rule_replaces_flag_no_duplicate():
    p = policy(allowed_item_categories=["sporting_goods"],
               rules=[rule("C3", "items[].item_category", "in", ["sporting_goods"])])
    assert len([r for r in evaluate_rules(facts(), p) if r.rule_id == "C3"]) == 1


# --- C5 specific item ------------------------------------------------------------------
@pytest.mark.parametrize("requested,item_name,match", [
    ("road-running shoes", "Road-running shoes", True),
    ("road-running shoes", "Trail-running shoes", False),
    ("27-inch monitor", "27-inch computer monitor", True),
    ("the 27-inch monitor I chose", "27-inch computer monitor", True),
    ("27-inch monitor", "Digital gift voucher", False),
    ("concert tickets", "Concert ticket", True),  # plurals folded
])
def test_requested_item_matching(requested, item_name, match):
    f = facts(lines=[{"item_name": item_name, "item_category": "x", "item_details": ""}])
    assert matches_requested_item(f.items[0], requested) is match


def test_c5_fail_names_what_was_in_the_cart():
    f = facts(lines=[{"item_name": "Cycling helmet", "item_category": "sporting_goods", "item_details": "size M"}])
    r = only(evaluate_rules(f, policy(requested_item="road-running shoes")), "C5")
    assert r.outcome == "fail" and "Cycling helmet" in r.detail


# --- C6 item details -------------------------------------------------------------------
SIZE43 = rule("C6", "items[].size_eu", "=", 43)


@pytest.mark.parametrize("details,outcome", [
    ("size 43", "pass"),
    ("size 42", "fail"),
    ("size 43.5", "fail" if _SIZE_EU_DECIMAL else "unknown"),  # half size != 43 (unknown until contract)
    ("no size mentioned", "unknown"),
    ("size 42, also size 43", "unknown"),  # contradiction
])
def test_c6_size(details, outcome):
    f = facts(lines=[{"item_name": "Road-running shoes", "item_category": "sporting_goods", "item_details": details}])
    assert only(evaluate_rules(f, policy(rules=[SIZE43], requested_item="road-running shoes")), "C6").outcome == outcome


def test_c6_contradiction_is_flagged_in_detail():
    f = facts(lines=[{"item_name": "Road-running shoes", "item_category": "sporting_goods",
                      "item_details": "size 42, also size 43"}])
    r = only(evaluate_rules(f, policy(rules=[SIZE43])), "C6")
    assert "contradicts itself" in r.detail


def test_c6_add_on_line_without_size_does_not_make_size_unknown():
    f = facts(lines=[
        {"item_name": "Road-running shoes", "item_category": "sporting_goods", "item_details": "size 43"},
        {"item_name": "Extended protection plan", "item_category": "subscriptions", "item_details": "billed monthly"},
    ])
    assert only(evaluate_rules(f, policy(rules=[SIZE43], requested_item="road-running shoes")), "C6").outcome == "pass"


@pytest.mark.skipif(not _HAS_SIZE_LETTER, reason="needs ItemFacts.size_letter (P2 contract request)")
def test_c6_letter_size():
    f = facts(lines=[{"item_name": "Jacket", "item_category": "clothing", "item_details": "size M"}])
    p = lambda v: policy(rules=[rule("C6", "items[].size_letter", "=", v)])
    assert only(evaluate_rules(f, p("M")), "C6").outcome == "pass"
    assert only(evaluate_rules(f, p("S")), "C6").outcome == "fail"


# --- C7 order terms --------------------------------------------------------------------
RET14 = rule("C7", "order.return_window_days", ">=", 14)


@pytest.mark.parametrize("details,returnable,outcome", [
    ("returns accepted within 14 days", "true", "pass"),   # 14 meets "14 or more"
    ("returns accepted within 30 days", "true", "pass"),
    ("returns accepted within 7 days", "true", "fail"),
    ("sold as final sale", "false", "fail"),
    ("returns accepted within 30 days", "false", "fail"),   # field says not returnable: stricter wins
    ("return policy not stated by the seller", "unknown", "unknown"),
    ("returns accepted within 30 days. Final sale.", "true", "unknown"),  # contradiction
])
def test_c7_return_window(details, returnable, outcome):
    f = facts(order_returnable=returnable,
              lines=[{"item_name": "Shoes", "item_category": "sporting_goods", "item_details": details}])
    assert only(evaluate_rules(f, policy(rules=[RET14])), "C7").outcome == outcome


def test_order_cancellable_unknown_is_unknown():
    r = only(evaluate_rules(facts(order_cancellable="unknown"),
                            policy(rules=[rule("C7", "order.order_cancellable", "=", "true")])), "C7")
    assert r.outcome == "unknown"


# --- C8 shop type ----------------------------------------------------------------------
def test_c8_shop_type():
    f = facts()
    assert only(evaluate_rules(f, policy(shop_type="sporting_goods")), "C8").outcome == "pass"
    f.merchant_category = "sustainable_goods"
    r = only(evaluate_rules(f, policy(shop_type="sporting_goods")), "C8")
    assert r.outcome == "fail" and "sustainable goods" in r.detail


# --- C9 known shop ---------------------------------------------------------------------
@pytest.mark.parametrize("known,outcome", [(True, "pass"), (False, "fail")])
def test_c9_known_shop(known, outcome):
    assert only(evaluate_rules(facts(known=known), policy(requires_known_shop=True)), "C9").outcome == outcome


def test_c9_not_checked_unless_asked():
    assert customer_results(evaluate_rules(facts(known=False), policy())) == []


# --- C10 nothing extra -----------------------------------------------------------------
def test_c10_extra_line_fails_and_says_what_to_remove():
    f = facts(lines=[
        {"item_name": "27-inch computer monitor", "item_category": "electronics", "item_details": ""},
        {"item_name": "Extended protection plan", "item_category": "subscriptions", "item_details": ""},
    ])
    r = only(evaluate_rules(f, policy(requested_item="27-inch monitor", nothing_extra=True)), "C10")
    assert r.outcome == "fail" and r.counterfactual == "Would approve without Extended protection plan"


def test_c10_only_requested_item_passes():
    f = facts(lines=[{"item_name": "27-inch computer monitor", "item_category": "electronics", "item_details": ""}])
    assert only(evaluate_rules(f, policy(requested_item="27-inch monitor", nothing_extra=True)), "C10").outcome == "pass"


# --- C12 other restrictions ------------------------------------------------------------
def test_c12_per_item_limit_and_quantity():
    f = facts(lines=[{"item_name": "Concert ticket", "item_category": "tickets", "item_details": "",
                      "unit_price": 90.0, "quantity": 2}])
    rs = evaluate_rules(f, policy(rules=[rule("P", "items[].unit_price_chf", "<=", 90),
                                         rule("Q", "items[].quantity", "=", 2)]))
    assert only(rs, "P").outcome == "pass" and only(rs, "Q").outcome == "pass"
    f.items[0].unit_price_chf = 90.01
    assert only(evaluate_rules(f, policy(rules=[rule("P", "items[].unit_price_chf", "<=", 90)])), "P").outcome == "fail"


def test_c12_country():
    p = policy(rules=[rule("K", "merchant.merchant_country", "=", "CH")])
    assert only(evaluate_rules(facts(), p), "K").outcome == "pass"
    f = facts()
    f.merchant_country = "GB"
    assert only(evaluate_rules(f, p), "K").outcome == "fail"


def test_c12_weekday_and_hour_in_zurich_time():
    weekdays = rule("W", "authorization.weekday", "in", ["mon", "tue", "wed", "thu", "fri"])
    assert only(evaluate_rules(facts(), policy(rules=[weekdays])), "W").outcome == "pass"  # Wednesday
    sat = facts(timestamp="2026-08-15T10:00:00Z")
    assert only(evaluate_rules(sat, policy(rules=[weekdays])), "W").outcome == "fail"
    assert only(evaluate_rules(facts(), policy(rules=[rule("H", "authorization.local_hour", "<", 13)])), "H").outcome == "pass"


def test_c12_delivery_date():
    by_friday = rule("DL", "authorization.delivery_by", "<=", "2026-08-14")
    assert only(evaluate_rules(facts(delivery_by="2026-08-13"), policy(rules=[by_friday])), "DL").outcome == "pass"
    assert only(evaluate_rules(facts(delivery_by="2026-08-15"), policy(rules=[by_friday])), "DL").outcome == "fail"
    assert only(evaluate_rules(facts(delivery_by=None), policy(rules=[by_friday])), "DL").outcome == "unknown"


def test_cart_recurring_uses_category_and_text():
    no_recurring = rule("R", "cart.recurring", "=", "false")
    f = facts(lines=[{"item_name": "Protection plan", "item_category": "subscriptions", "item_details": ""}])
    assert only(evaluate_rules(f, policy(rules=[no_recurring])), "R").outcome == "fail"
    assert only(evaluate_rules(facts(), policy(rules=[no_recurring])), "R").outcome == "pass"


# --- C2 period limit (M4, M5) ------------------------------------------------------------
WEEK = rule("C2", ORDER, "<=", 300, scope="period", period_days=7)


def test_period_rules_are_not_in_evaluate_rules():
    p = policy(rules=[WEEK])
    assert customer_results(evaluate_rules(facts(), p)) == [] and period_rules(p) == [WEEK]


def test_period_equal_limit_passes():
    f = facts(amount=65.5, billing_amount_chf=65.5)
    assert evaluate_period_rule(WEEK, f, spent_chf=234.50, reserved_chf=0).outcome == "pass"  # exactly 300.00


def test_period_over_limit_on_finals_fails():
    f = facts(amount=24.0, billing_amount_chf=24.0)
    r = evaluate_period_rule(WEEK, f, spent_chf=300.00, reserved_chf=0)
    assert r.outcome == "fail" and RESERVATION_ONLY not in r.detail
    assert r.detail == "This would take the week to CHF 324.00, over your CHF 300.00"
    assert r.counterfactual == "Nothing more fits this week"


@pytest.mark.parametrize(("days", "clause", "counterfactual"), [
    (1, "This would take the day to CHF 120.00, over your CHF 100.00", "Would approve at CHF 80.00 or less today"),
    (7, "This would take the week to CHF 120.00, over your CHF 100.00", "Would approve at CHF 80.00 or less this week"),
    (30, "This would take the month to CHF 120.00, over your CHF 100.00", "Would approve at CHF 80.00 or less this month"),
    (14, "This would take the 14 days to CHF 120.00, over your CHF 100.00",
     "Would approve at CHF 80.00 or less in these 14 days"),
])  # fmt: skip
def test_period_template_names_the_period(days, clause, counterfactual):
    period = WEEK.model_copy(update={"value": 100, "period_days": days})
    r = evaluate_period_rule(period, facts(amount=100.0, billing_amount_chf=100.0), spent_chf=20.0, reserved_chf=0)
    assert (r.outcome, r.detail, r.counterfactual) == ("fail", clause, counterfactual)


def test_period_fail_only_because_of_reservation_is_marked():
    f = facts(amount=65.5, billing_amount_chf=65.5)
    r = evaluate_period_rule(WEEK, f, spent_chf=234.50, reserved_chf=65.00)
    assert r.outcome == "fail" and RESERVATION_ONLY in r.detail
    assert "CHF 65.00" in r.counterfactual


def test_period_headroom_counterfactual():
    f = facts(amount=138.0, billing_amount_chf=138.0)
    r = evaluate_period_rule(WEEK, f, spent_chf=255.50, reserved_chf=0)
    assert r.outcome == "fail" and r.counterfactual == "Would approve at CHF 44.50 or less this week"
