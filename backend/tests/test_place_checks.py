"""Where the shop is: "in Munich" / "in Switzerland" are checks on the event's trusted
merchant_city / merchant_country (lanes P4 compiler, P2 engine), and the wording of a
restriction no data can check.

SCEN0124 ("Book me a hotel in Munich ...") compiled "in Munich" as a restriction no data
can check, so SummitStay (Lucerne, CH) reached the customer as a question although the
event names the shop's city. Now both compiler paths write merchant.merchant_city =
Munich (exact), the Lucerne hotel declines, a Munich hotel passes, and an event with no
city is unknown, never a pass (rule 4).
"""

from __future__ import annotations

from datetime import date

import pytest

from oneguard.compiler import compile_instruction
from oneguard.compiler.parser import parse, places
from oneguard.engine.decide import decide
from oneguard.engine.explain import explain
from oneguard.engine.facts import build_facts
from oneguard.engine.policy import (
    evaluate_rules,
    evaluate_typed_rule,
    unverifiable_detail,
)
from oneguard.engine.types import Policy, Rule
from oneguard.llm.provider import NullProvider
from tests.test_compiler_judging import RECORDED, SERVED, Scripted
from tests.test_protections import view

CITY = "merchant.merchant_city"
COUNTRY = "merchant.merchant_country"
TODAY = date(2026, 9, 1)
SCEN0124 = SERVED["SCEN0124"]
SUMMITSTAY = ("ME0040", "SummitStay", "CH", "Lucerne")  # merchants.csv
ISARNEST = ("ME0041", "IsarNest Hotel", "DE", "Munich")


def _recorded(scenario: str) -> dict:
    return next(e["response"] for e in RECORDED if e["scenario"] == scenario)


def _drafts():
    """SCEN0124 through the parser alone, the fallback, and the recorded model response."""
    return {
        "parser": parse(SCEN0124, None, "", today=TODAY),
        "fallback": compile_instruction(SCEN0124, None, "", NullProvider(), today=TODAY),
        "llm": compile_instruction(SCEN0124, None, "", Scripted(_recorded("SCEN0124")), today=TODAY),
    }


def _policy(draft) -> Policy:
    return Policy(mandate_id="MD-T", status="active", instruction=draft.instruction, rules=draft.rules,
                  uncertainty_policy=draft.uncertainty_policy)


def _event(shop: tuple[str, str, str, str | None], cancellable: str = "true") -> dict:
    merchant_id, name, country, city = shop
    merchant = {"merchant_id": merchant_id, "merchant_name": name, "merchant_category": "hotel",
                "merchant_country": country, "merchant_mcc": "7011", "recurring_capable": "false"}
    if city is not None:
        merchant["merchant_city"] = city
    return {"authorization": {
        "authorization_id": f"AU-T-{merchant_id}", "merchant": merchant,
        "timestamp": "2026-09-02T10:00:00Z", "amount": 540.0, "currency": "CHF", "billing_amount_chf": 540.0,
        "customer_device_id": "DVC-1", "recent_attempt_count_10m": 0, "delivery_by": None,
        "order_returnable": "true", "order_cancellable": cancellable,
        "related_authorization_id": None, "related_authorization_status": None,
        "items": [{"line_no": 1, "item_id": "IT0012", "item_name": "Hotel room", "item_category": "hotel",
                   "quantity": 3, "unit_price": 180.0, "currency": "CHF", "item_details": "3 nights"}],
    }}


# --- the compiler: places (SCEN0124 on both paths: test_compiler_unseen.py) ---------
@pytest.mark.parametrize(("text", "expected"), [
    ("Book me a hotel in Munich for 3 nights", [(CITY, "=", "Munich")]),
    ("Buy running shoes from shops in Switzerland", [(COUNTRY, "=", "CH")]),
    ("A hotel in Zürich, max CHF 200", [(CITY, "=", "Zurich")]),
    ("Book a room in St. Gallen", [(CITY, "=", "St. Gallen")]),
    ("Order a jacket in the UK, max GBP 90", [(COUNTRY, "=", "GB")]),
    ("A hotel, but not in Germany", [(COUNTRY, "!=", "DE")]),
    ("No shops in Italy", [(COUNTRY, "!=", "IT")]),
    ("A hotel with no breakfast in Munich", [(CITY, "=", "Munich")]),  # the "no" is the breakfast's
    ("Groceries delivered to me in Zurich", []),                  # where it arrives, not the shop
    ("Pay in US dollars, max USD 50", []),                          # a currency, not a country
    ("Book a hotel in September", []),
    ("Book a hotel in Paris for 2 nights", []),                     # not in the catalogue: stays a U rule
], ids=["city", "country", "umlaut", "two-words", "the-uk", "not-in", "no-shops", "no-breakfast", "delivered", "currency",
        "month", "unknown-city"])
def test_places(text, expected):
    assert [(s.field, s.operator, s.value) for s in places(text, [])] == expected


def test_a_place_the_catalogue_does_not_hold_stays_a_restriction_no_data_can_check():
    draft = parse("Book me a hotel in Paris for 2 nights, at most CHF 150 per night", None, "", today=TODAY)
    assert not any(r.field == CITY for r in draft.rules)
    assert [r.value for r in draft.rules if r.field == "unverifiable"] == ["a hotel in Paris", "for 2 nights"]


def test_an_adjective_and_a_place_naming_one_country_are_one_rule():
    draft = parse("Order hiking boots from a German outdoor retailer in Germany, max EUR 150", None, "", today=TODAY)
    assert [(r.field, r.value) for r in draft.rules if r.field == COUNTRY] == [(COUNTRY, "DE")]


# --- the engine: the city check on the event ------------------------------------------
def _city_result(shop):
    policy = _policy(_drafts()["parser"])
    facts = build_facts(_event(shop))
    return next(r for r in evaluate_rules(facts, policy) if r.rule_id == "C12-city")


def test_a_lucerne_hotel_fails_the_munich_check():
    res = _city_result(SUMMITSTAY)
    assert res.outcome == "fail"
    assert res.detail == "SummitStay is in Lucerne, not Munich"
    assert res.counterfactual == "Would approve at a shop in Munich"


def test_a_munich_hotel_passes_the_munich_check():
    assert _city_result(ISARNEST).outcome == "pass"


def test_no_city_on_the_event_is_unknown_never_a_pass():
    res = _city_result(("ME0099", "Nameless Stay", "DE", None))
    assert res.outcome == "unknown"
    assert "shop city not stated" in res.detail


def test_scen0124_declines_the_lucerne_hotel_with_the_city_as_the_reason():
    policy = _policy(_drafts()["llm"])
    facts = build_facts(_event(SUMMITSTAY))
    rules = evaluate_rules(facts, policy)
    decision = decide(rules, [], [], [], policy, view())
    assert decision.outcome == "decline"
    assert "C12-city" in decision.deciding_ids
    message = explain(decision, facts, policy, rules, []).message
    assert message.startswith("Declined CHF 540.00: ") and "Munich" in message


# --- a restriction no data can check: the wording ------------------------------------
def test_the_unverifiable_message_quotes_the_phrase_once():
    rule = Rule(id="U1", field="unverifiable", operator="=", value="for 3 nights from 10 September to 13 September",
                text='"for 3 nights from 10 September to 13 September" (no data can check this; you will be asked)',
                source="exact", kind="other")
    facts = build_facts(_event(ISARNEST))
    res = evaluate_typed_rule(rule, facts, _policy(_drafts()["parser"]))
    assert res.outcome == "unknown"
    assert res.detail == unverifiable_detail(rule) == \
        'Can\'t check "for 3 nights from 10 September to 13 September" from the data; you decide'


def test_the_step_up_message_ends_with_you_decide():
    policy = _policy(_drafts()["parser"])
    facts = build_facts(_event(ISARNEST))
    rules = evaluate_rules(facts, policy)
    decision = decide(rules, [], [], [], policy, view())
    assert decision.outcome == "step_up"
    assert decision.deciding_ids == ["U1"]
    message = explain(decision, facts, policy, rules, []).message
    assert message.endswith(': Can\'t check "for 3 nights from 10 September to 13 September" from the data; you decide.')
    assert message.count('"') == 2


def test_a_field_outside_the_vocabulary_is_worded_the_same():
    rule = Rule(id="U9", field="merchant.official_seller", operator="=", value="true", text="official ticket seller",
                source="exact")
    res = evaluate_typed_rule(rule, build_facts(_event(ISARNEST)), _policy(_drafts()["parser"]))
    assert res.detail == 'Can\'t check "official ticket seller" from the data; you decide'
