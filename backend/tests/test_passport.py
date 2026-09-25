"""docs/passport.md: canonical JSON, signatures, documents and would_approve_if."""

from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from oneguard.engine.explain import rule_bound, would_approve_if
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import (
    EngineDecision,
    EvidenceRow,
    Policy,
    Rule,
    RuleResult,
    Signal,
)
from oneguard.passport.canonical import canonical, money, sha256_hex
from oneguard.passport.documents import permitted, receipt_body
from oneguard.passport.keys import KeyRing
from oneguard.replay.events import Pack
from oneguard.replay.matrix import POLICIES, _keyword_signals
from oneguard.replay.runner import decide_all, load_policy
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.schema import Decision, EventRaw

NOW = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


@pytest.fixture
def keys(tmp_path: Path) -> KeyRing:
    db = make_engine(f"sqlite:///{tmp_path / 'keys.sqlite'}")
    from oneguard.store.db import init_db

    init_db(db)
    return KeyRing.load_or_create(db, lambda: NOW)


# Canonical JSON ----------------------------------------------------------------------------


def test_the_same_object_always_gives_the_same_bytes() -> None:
    a = {"b": [1, 2.0, {"z": None, "a": True}], "a": "é", "amount": 520, "at": NOW}
    b = {"at": NOW, "amount": 520.0, "a": "é", "b": [1, 2, {"a": True, "z": None}]}
    assert canonical(a) == canonical(b)
    assert canonical(a) == canonical(json.loads(canonical(a)))  # what a verifier re-reads
    assert canonical(a) == (
        '{"a":"é","amount":"520.00","at":"2026-09-25T10:00:00Z","b":[1,2,{"a":true,"z":null}]}'.encode()
    )


def test_money_is_two_decimals_half_even_and_other_numbers_are_shortest() -> None:
    assert money(0.125) == "0.12" and money(0.135) == "0.14" and money("399.9") == "399.90"
    assert canonical({"unit_price": 18, "billing_amount_chf": 44.5, "value": 399.9, "quantity": 2.0}) == (
        b'{"billing_amount_chf":"44.50","quantity":2,"unit_price":"18.00","value":399.9}'
    )
    with pytest.raises(ValueError):
        canonical({"value": float("nan")})
    with pytest.raises(ValueError):
        canonical({"at": datetime(2026, 9, 25)})  # noqa: DTZ001 - naive on purpose


# Signatures --------------------------------------------------------------------------------


def test_a_signature_round_trips_and_one_changed_byte_breaks_it(keys: KeyRing) -> None:
    document = {"type": "oneguard.passport/1", "card_id": "CA0001", "issuer": {"name": "OneGuard", "key_id": keys.active_key_id}}
    signature, key_id = keys.sign(document)
    assert key_id == keys.active_key_id and key_id.startswith("ogk_")
    assert keys.verify(document, signature, key_id)

    tampered = copy.deepcopy(document)
    tampered["card_id"] = "CA0002"  # one byte of the canonical form
    assert canonical(tampered) != canonical(document) and len(canonical(tampered)) == len(canonical(document))
    assert not keys.verify(tampered, signature, key_id)

    raw = bytearray(base64.b64decode(signature))
    raw[0] ^= 1
    assert not keys.verify(document, base64.b64encode(bytes(raw)).decode(), key_id)
    assert not keys.verify(document, signature, "ogk_unknown")
    assert not keys.verify(document, "not base64!", key_id)


def test_one_key_is_active_and_a_restart_keeps_it(tmp_path: Path) -> None:
    db = make_engine(f"sqlite:///{tmp_path / 'k.sqlite'}")
    from oneguard.store.db import init_db

    init_db(db)
    first = KeyRing.load_or_create(db)
    again = KeyRing.load_or_create(db)
    assert first.active_key_id == again.active_key_id
    assert [k.key_id for k in again.public_keys()] == [first.active_key_id]
    assert again.public_keys()[0].public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")


# Documents ---------------------------------------------------------------------------------


def _entry(**changes: Any) -> LedgerEntry:
    base = dict(  # noqa: C408 - keyword form reads as the LedgerEntry
        live_authorization_id="live_1", run_id="run_1", mandate_id="md_1", card_id="CA0001", customer_id="CU0001",
        ts_sim=NOW, outcome="decline", final=True, uncertain_outcome=None, merchant_id="ME0001", item_ids=["IT1"],
        billing_amount_chf=520.0, step=2, deciding_ids=["amount"], reason_codes=["per_order_limit_exceeded"],
        evidence=[
            EvidenceRow(rule="Total at or below CHF 400 per order", outcome="fail", detail="CHF 520.00 is over", source="policy"),
            EvidenceRow(rule="Card active", outcome="pass", detail="The card is active", source="policy"),
            EvidenceRow(rule="Instructions in shop text", outcome="fail", detail="…", source="merchant_text"),
        ],
        message="Declined CHF 520.00: …", engine_version="oneguard/0.0.0", latency_ms=1.0, signals_enabled=False,
        decided_at=NOW, would_approve_if=[{"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400}],
    )  # fmt: skip
    return LedgerEntry.model_validate({**base, **changes})


def test_permitted_names_each_evaluated_check_once_and_never_a_signal() -> None:
    checks = [{"id": "amount", "text": "Total at or below CHF 400 per order"}, {"id": "returns", "text": "Returns 14 days"}]
    rows = [r.model_dump(mode="json") for r in _entry().evidence]
    assert permitted(rows, checks) == [
        {"check_id": "amount", "outcome": "fail"},
        {"check_id": "card_status", "outcome": "pass"},
    ]


def test_a_receipt_carries_hashes_money_strings_and_the_passport_in_force() -> None:
    event = {"authorization": {"amount": 520, "currency": "CHF", "items": [{"item_id": "IT1", "unit_price": 520}]}}
    body = receipt_body(_entry(), event, "SRC1", ("pp_1", 2), [{"id": "amount", "text": "Total at or below CHF 400 per order"}], None, "ogk_1")
    assert body["type"] == "oneguard.receipt/1"
    assert (body["passport_id"], body["passport_version"]) == ("pp_1", 2)
    assert body["authorization"]["amount"] == "520.00" and body["authorization"]["billing_amount_chf"] == "520.00"
    assert body["authorization"]["items_hash"] == sha256_hex(event["authorization"]["items"])
    assert body["evidence_hash"] == sha256_hex([r.model_dump(mode="json") for r in _entry().evidence])
    assert body["would_approve_if"] == [{"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400}]
    assert body["resolution"] is None and body["decided_at"] == "2026-09-25T10:00:00Z"
    assert canonical(body) == canonical(json.loads(json.dumps(body)))  # stored as it was signed


def test_a_step_up_receipt_resolution_names_who_answered() -> None:
    answered = _entry(
        outcome="step_up", uncertain_outcome="approved", resolved_by="customer", resolved_at=NOW + timedelta(seconds=30),
        would_approve_if=None,
    )
    body = receipt_body(answered, None, None, None, [], "dev_1", "ogk_1")
    assert body["resolution"] == {
        "outcome": "approve", "resolved_by": "customer", "device_id": "dev_1", "resolved_at": "2026-09-25T10:00:30Z",
    }  # fmt: skip
    assert body["authorization"]["amount"] is None and body["authorization"]["items_hash"] is None  # no event: null
    expired = _entry(outcome="step_up", uncertain_outcome="expired", resolved_by="timeout", resolved_at=NOW)
    assert receipt_body(expired, None, None, None, [], "dev_1", "ogk_1")["resolution"]["device_id"] is None
    pending = _entry(outcome="step_up", final=False, uncertain_outcome="pending")
    assert receipt_body(pending, None, None, None, [], None, "ogk_1")["resolution"] is None


# would_approve_if --------------------------------------------------------------------------


def _decided(outcome: str) -> EngineDecision:
    return EngineDecision(outcome=outcome, reason_codes=["rule_not_met"], step=2, deciding_ids=[])  # type: ignore[arg-type]


def test_would_approve_if_is_the_failing_rules_bounds_on_a_decline_only() -> None:
    amount = RuleResult(rule_id="a", outcome="fail", detail="x", counterfactual="Would approve at CHF 400.00 or less",
                        source="event", counterfactual_bound={"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400})  # fmt: skip
    extra = RuleResult(rule_id="C10", outcome="fail", detail="x", counterfactual="Would approve without Plan",
                       source="event", counterfactual_bound={"remove_items": ["IT2"]})  # fmt: skip
    more = RuleResult(rule_id="C3", outcome="fail", detail="x", counterfactual="Would approve without Toy",
                      source="event", counterfactual_bound={"remove_items": ["IT3", "IT2"]})  # fmt: skip
    injected = Signal(id="A1", triggered=True, strength="protection", outcome_if_triggered="decline", detail="x",
                      source="merchant_text", counterfactual_bound={"requires": "clean_merchant_text"})  # fmt: skip
    assert would_approve_if(_decided("decline"), [amount, extra, more], [injected]) == [
        {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400},
        {"remove_items": ["IT2", "IT3"]},
    ]
    assert would_approve_if(_decided("decline"), [], [injected]) == [{"requires": "clean_merchant_text"}]
    assert would_approve_if(_decided("step_up"), [amount], [injected]) is None
    assert would_approve_if(_decided("approve"), [], []) is None


def test_a_limit_in_another_currency_is_bounded_in_chf() -> None:
    from oneguard.engine.types import LedgerView

    rule = Rule(id="eur", field="authorization.billing_amount_chf", operator="<=", value=100, currency="EUR",
                scope="purchase", text="At most EUR 100", source="exact")  # fmt: skip
    policy = Policy(mandate_id="m", status="active", instruction="", rules=[rule], uncertainty_policy="ask")
    view = LedgerView(period_spent_chf=0, period_reserved_chf=0, period_window_start=NOW, priors=[],
                      known_merchant_ids=set(), known_merchant_ids_on_card=set(), known_device_ids=set(),
                      known_countries=set(), max_approved_chf=None, flagged_merchant_ids=set(), frozen=False)  # fmt: skip
    result = RuleResult(rule_id="eur", outcome="fail", detail="x", counterfactual="Would approve …", source="event")
    assert rule_bound(result, policy, None, view) == {  # type: ignore[arg-type]
        "field": "authorization.billing_amount_chf", "operator": "<=", "value": 95,
    }  # fmt: skip


@pytest.fixture(scope="module")
def scen0004(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Decision]:
    """SCEN0004 through the real pipeline (keyword signals, as the matrix), by source id."""
    path = tmp_path_factory.mktemp("scen0004") / "store.sqlite"
    db = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=db)
    pack = Pack.load()
    with _keyword_signals():
        policy = load_policy(POLICIES / "SCEN0004.yaml", mandate_id="TM_REPLAY_SCEN0004")
        decide_all(pack, "SCEN0004", policy, True, db=db)
    with session(db) as s:
        source = {e.live_authorization_id: e.source_authorization_id for e in s.scalars(select(EventRaw))}
        return {source[d.live_authorization_id]: d for d in s.scalars(select(Decision))}


def test_au0037_would_approve_at_400_or_less(scen0004: dict[str, Decision]) -> None:
    decided = scen0004["AU0037"]
    assert decided.outcome == "decline"
    assert decided.would_approve_if == [{"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400}]
    assert decided.receipt_id is not None and decided.receipt_id.startswith("rc_")


def test_au0041_would_approve_within_the_limit_and_without_the_protection_plan(scen0004: dict[str, Decision]) -> None:
    bounds = scen0004["AU0041"].would_approve_if
    assert {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400} in bounds
    removal = next(b for b in bounds if "remove_items" in b)
    assert removal["remove_items"] and set(removal["remove_items"]) <= set(scen0004["AU0041"].item_ids)


def test_au0039_would_approve_at_a_known_shop(scen0004: dict[str, Decision]) -> None:
    assert {"requires": "known_shop"} in scen0004["AU0039"].would_approve_if


def test_asks_and_approvals_have_no_would_approve_if(scen0004: dict[str, Decision]) -> None:
    for decided in scen0004.values():
        if decided.outcome != "decline":
            assert decided.would_approve_if is None, decided.live_authorization_id
        else:
            assert decided.would_approve_if, decided.live_authorization_id
