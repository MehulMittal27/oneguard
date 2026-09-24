"""Warning signs with no baseline yet (rules.md §8 W-rule 5).

A customer with no approved purchase in history and none in this run has no usual device,
country or amount: W1, W3 and W4 are "no baseline yet" info, never an ask. From the first
final approval in the run, that purchase's device and country are known (read from the
stored event, as the worker and the offline replay store it) and its amount is the largest.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from oneguard.engine.facts import build_facts
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.types import Policy
from oneguard.engine.warnings import warning_signs
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store.db import init_db, make_engine
from oneguard.store.schema import EventRaw
from tests.test_c9_no_history import C1, CARD, MANDATE, NEW, NOW, event, history

LIMIT_ONLY = Policy(mandate_id=MANDATE, status="active", instruction="Groceries up to CHF 50.",
                    uncertainty_policy="ask", rules=[C1])
BASELINE_IDS = ("W1", "W3", "W4")


@pytest.fixture
def ledger(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'baseline.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield StoreLedger(s, history=history())
    engine.dispose()


def decide(ledger: StoreLedger, ev: dict):
    """Store the event first, as the worker and the offline replay do, then decide it."""
    auth = ev["authorization"]
    ledger.session.add(EventRaw(live_authorization_id=auth["authorization_id"], run_id="run-new",
                                source_authorization_id=auth["source_authorization_id"],
                                received_at=NOW, deadline_at=NOW, event=ev))
    ledger.session.commit()
    ctx = PipelineContext(policy=LIMIT_ONLY, ledger=ledger, history=history(), run_id="run-new", now=lambda: NOW)
    return decide_event(ev, ctx)


def with_device(ev: dict, device: str, country: str = "CH") -> dict:
    ev["authorization"]["customer_device_id"] = device
    ev["authorization"]["merchant"]["merchant_country"] = country
    return ev


def signs(ledger: StoreLedger, ev: dict) -> dict:
    facts = build_facts(ev, history())
    view = ledger.view(run_id="run-new", customer_id=NEW, card_id=CARD, at=facts.timestamp, period_days=None)
    return {s.id: s for s in warning_signs(facts, view, LIMIT_ONLY)}


def test_first_purchase_has_no_baseline_and_is_approved(ledger):
    first = with_device(event(amount=80.0), "DVC-NEW", "DE")  # new device, country, amount: all unknown
    got = signs(ledger, first)
    for sid in BASELINE_IDS:
        assert (got[sid].triggered, got[sid].outcome_if_triggered) == (True, "info"), sid
        assert got[sid].detail.startswith("No baseline yet: "), sid

    engine, explanation, _ = decide(ledger, with_device(event(), "DVC-NEW", "DE"))
    assert (engine.outcome, engine.step, engine.session_trust) == ("approve", 7, "normal")
    info = {row.rule: row for row in explanation.evidence if row.outcome == "info"}
    assert info["New device"].detail.startswith("No baseline yet")
    assert info["New country"].detail.startswith("No baseline yet")


def test_after_the_first_approval_the_same_device_is_known(ledger):
    engine, _, _ = decide(ledger, with_device(event(), "DVC-NEW", "DE"))
    assert engine.outcome == "approve"

    again = with_device(event(item="IT_B", minutes=60), "DVC-NEW", "DE")
    got = signs(ledger, again)
    assert not got["W1"].triggered and not got["W3"].triggered
    engine, _, _ = decide(ledger, again)
    assert engine.outcome == "approve"


def test_after_the_first_approval_a_new_device_asks(ledger):
    decide(ledger, with_device(event(), "DVC-NEW"))
    engine, explanation, _ = decide(ledger, with_device(event(item="IT_B", minutes=60), "DVC-OTHER"))
    assert (engine.outcome, engine.step, engine.deciding_ids) == ("step_up", 6, ["W1"])
    assert explanation.message == "Waiting for you CHF 30.00: made from a device you have not used before."


def test_after_the_first_approval_its_amount_is_the_baseline(ledger):
    decide(ledger, with_device(event(amount=20.0), "DVC-NEW"))
    got = signs(ledger, with_device(event(minutes=60, amount=45.0), "DVC-NEW"))
    assert got["W4"].outcome_if_triggered == "ask" and not got["W4"].triggered  # within the CHF 50 limit
    unlimited = Policy(mandate_id=MANDATE, status="active", instruction="Groceries.", uncertainty_policy="ask", rules=[])
    facts = build_facts(with_device(event(minutes=60, amount=45.0), "DVC-NEW"), history())
    view = ledger.view(run_id="run-new", customer_id=NEW, card_id=CARD, at=facts.timestamp, period_days=None)
    w4 = {s.id: s for s in warning_signs(facts, view, unlimited)}["W4"]
    assert w4.triggered and "largest approved purchase (CHF 20.00)" in w4.detail


def test_a_missing_device_id_still_triggers_w1(ledger):
    ev = event()
    ev["authorization"]["customer_device_id"] = None
    w1 = signs(ledger, ev)["W1"]
    assert (w1.triggered, w1.outcome_if_triggered) == (True, "ask")


def test_a_declined_or_pending_purchase_starts_no_baseline(ledger):
    engine, _, _ = decide(ledger, with_device(event(amount=80.0), "DVC-NEW"))  # over the CHF 50 limit
    assert engine.outcome == "decline"
    got = signs(ledger, with_device(event(minutes=60), "DVC-OTHER"))
    assert got["W1"].outcome_if_triggered == "info"
