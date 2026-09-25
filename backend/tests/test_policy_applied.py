"""What a decision screen shows about the rules that decided it.

- A platform mandate's own rules (``hard_rules``, used when no confirmed policy is bound)
  are worded like compiled rules: "Each item at or below CHF 300", never
  "items[].unit_price_chf <= 300 CHF".
- ``Decision.policy_applied`` carries the checks the decision was made under: ``platform``
  for the platform mandate's rules, ``confirmed`` for our own policy; C6 rebuilds the
  platform policy from the stored event, so a reload shows the same checks.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from oneguard.api.routes_customer import decided_by_platform
from oneguard.compiler.draft import describe_rule
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import LedgerView, Policy, Rule
from oneguard.pipeline import policy_applied
from oneguard.replay.events import all_events
from oneguard.viseca.worker import policy_from_snapshot

HARD_RULES = [
    {"field": "items[].unit_price_chf", "operator": "<=", "value": 300, "currency": "CHF", "scope": "purchase"},
    {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 300, "currency": "CHF",
     "scope": "period", "period_days": 7},
    {"field": "items[].item_category", "operator": "in", "value": ["electronics"]},
    {"field": "merchant.familiar_on_card", "operator": "=", "value": "true"},
    {"field": "merchant.known_shop", "operator": "=", "value": "true"},
    {"field": "merchant.merchant_category", "operator": "=", "value": "sporting_goods"},
    {"field": "not.a.field", "operator": "=", "value": "x"},
]


@pytest.mark.parametrize("raw,text", [
    (HARD_RULES[0], "Each item at or below CHF 300"),
    (HARD_RULES[1], "Total at or below CHF 300 across any 7 days"),
    (HARD_RULES[2], "Only electronics"),
    (HARD_RULES[3], "Only shops you have bought from before"),
    (HARD_RULES[4], "Only shops you have bought from before"),
    (HARD_RULES[5], "Only from a sporting goods shop"),
])
def test_platform_rules_are_worded_like_compiled_rules(raw, text):
    policy = policy_from_snapshot({"mandate_id": "TM_1", "hard_rules": [raw]})
    assert policy.rules[0].text == text
    assert describe_rule(raw["field"], raw["operator"], raw["value"], currency=raw.get("currency"),
                         scope=raw.get("scope"), period_days=raw.get("period_days")) == text


def test_a_rule_outside_the_vocabulary_keeps_its_raw_form():
    assert describe_rule("not.a.field", "=", "x") is None
    policy = policy_from_snapshot({"mandate_id": "TM_1", "hard_rules": [HARD_RULES[6]]})
    assert policy.rules[0].text == "not.a.field = x"


def _event() -> dict:
    event = copy.deepcopy(all_events()["SCEN0003"][0])
    event["mandate"]["mandate_id"] = "TM_LIVE"
    event["mandate"]["hard_rules"] = HARD_RULES[:3]
    return event


def _entry(mandate_id: str) -> LedgerEntry:
    now = datetime(2026, 8, 25, 8, tzinfo=UTC)
    return LedgerEntry(
        live_authorization_id="AU_LIVE_1", run_id="run_1", mandate_id=mandate_id, card_id="CA1576",
        customer_id="CU_1", ts_sim=now, outcome="decline", final=True, uncertain_outcome=None,
        merchant_id="ME_QP", item_ids=["IT_X"], billing_amount_chf=351.65, related_live_id=None,
        relation=None, session_trust="normal", step=2, deciding_ids=["hard_rule_1"], reason_codes=["rule_failed"],
        evidence=[], message="Declined", counterfactual=None, explanation_source="template",
        injection_flag=None, engine_version="test", latency_ms=1.0, signals_enabled=False, decided_at=now,
        deadline_at=None,
    )


def test_a_platform_decision_says_so_and_lists_what_decided():
    event = _event()
    policy = policy_from_snapshot(event["mandate"])
    applied = policy_applied(event, _entry("TM_LIVE"), policy)
    assert applied is not None and applied.source == "platform" and applied.mandate_id == "TM_LIVE"
    assert [c.text for c in applied.checks] == [
        "Each item at or below CHF 300", "Total at or below CHF 300 across any 7 days", "Only electronics"]


def test_a_confirmed_policy_is_confirmed():
    policy = Policy(mandate_id="M_OURS", status="active", instruction="Only cosmetics", uncertainty_policy="ask",
                    rules=[Rule(id="C3", field="items[].item_category", operator="in", value=["cosmetics"],
                                text="Only cosmetics", source="exact", kind="item")])
    applied = policy_applied(_event(), _entry("M_OURS"), policy)
    assert applied is not None and applied.source == "confirmed"
    assert [c.text for c in applied.checks] == ["Only cosmetics"]


def test_the_requested_item_and_nothing_extra_are_listed_after_the_rules():
    """C5 / C10 are flags, not rules; the decision still shows them as the draft did."""
    policy = Policy(mandate_id="M_OURS", status="active", instruction="a monitor", uncertainty_policy="ask",
                    rules=[Rule(id="C1", field="authorization.billing_amount_chf", operator="<=", value=400,
                                currency="CHF", scope="purchase", text="Total at or below CHF 400 per order",
                                source="exact", kind="amount")],
                    requested_item="27-inch monitor", nothing_extra=True)
    applied = policy_applied(_event(), _entry("M_OURS"), policy)
    assert applied is not None
    assert [(c.id, c.text, c.source) for c in applied.checks] == [
        ("C1", "Total at or below CHF 400 per order", "exact"),
        ("requested_item", "Only the item you asked for: 27-inch monitor", "exact"),
        ("nothing_extra", "Nothing added that you didn't ask for", "exact"),
    ]

def test_a_policy_without_rules_sends_nothing():
    empty = Policy(mandate_id="M_X", status="active", instruction="", rules=[], uncertainty_policy="ask")
    assert policy_applied(_event(), _entry("M_X"), empty) is None


def test_c6_rebuilds_the_platform_policy_from_the_stored_event():
    """A reload (C6) finds no stored mandate for TM_LIVE: the checks come from the event."""
    policy = decided_by_platform(_event(), _entry("TM_LIVE"))
    assert policy.rules[0].text == "Each item at or below CHF 300"
    other = decided_by_platform(_event(), _entry("M_REPLAY"))
    assert other.rules == []


def test_the_contract_decision_carries_it():
    from oneguard.pipeline import to_api_decision

    event = _event()
    policy = policy_from_snapshot(event["mandate"])
    view = LedgerView(period_spent_chf=0, period_reserved_chf=0, period_window_start=datetime(2026, 8, 1, tzinfo=UTC),
                      priors=[], known_merchant_ids=set(), known_merchant_ids_on_card=set(), known_device_ids=set(),
                      known_countries=set(), max_approved_chf=None, flagged_merchant_ids=set(), frozen=False)
    decision = to_api_decision(event, _entry("TM_LIVE"), view, policy)
    body = decision.model_dump(mode="json")
    assert body["policy_applied"]["source"] == "platform"
    assert body["policy_applied"]["checks"][0]["text"] == "Each item at or below CHF 300"
