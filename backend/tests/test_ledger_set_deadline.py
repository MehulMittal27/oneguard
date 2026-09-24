"""``StoreLedger.set_deadline`` (added by P1 for the Viseca worker; P2 to review).

The worker moves a pending step-up's deadline to Viseca's accepted time + the human
window. Same contract as ``InMemoryLedger``: only a pending step-up moves; the new
deadline is committed; nothing else changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from oneguard.engine.explain import expired_message
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger, LedgerEntry
from oneguard.store.db import init_db, make_engine

TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
DECIDED = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def entry(live_id: str, outcome: str = "step_up") -> LedgerEntry:
    return LedgerEntry(
        live_authorization_id=live_id,
        run_id="run-1",
        mandate_id="M1",
        card_id="CA1",
        customer_id="CU1",
        ts_sim=TS,
        outcome=outcome,
        final=outcome != "step_up",
        uncertain_outcome="pending" if outcome == "step_up" else None,
        merchant_id="ME1",
        item_ids=["IT1"],
        billing_amount_chf=42.5,
        step=6,
        deciding_ids=["rule-1"],
        reason_codes=["stub"],
        evidence=[],
        message="Please confirm.",
        engine_version="oneguard/test",
        latency_ms=1.0,
        signals_enabled=False,
        decided_at=DECIDED,
        deadline_at=DECIDED + timedelta(seconds=120) if outcome == "step_up" else None,
    )


@pytest.fixture(params=["store", "memory"])
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Ledger]:
    if request.param == "memory":
        yield InMemoryLedger()
        return
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield StoreLedger(s)
    engine.dispose()


def test_set_deadline_moves_only_the_deadline_of_a_pending_step_up(ledger: Ledger) -> None:
    stored = ledger.record(entry("lv_1"))
    later = DECIDED + timedelta(seconds=130)
    moved = ledger.set_deadline("lv_1", later)
    assert moved.deadline_at == later
    assert moved.model_dump(exclude={"deadline_at"}) == stored.model_dump(exclude={"deadline_at"})
    assert ledger.get("lv_1") == moved


def test_set_deadline_is_committed(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    later = DECIDED + timedelta(seconds=130)
    with Session(engine, expire_on_commit=False) as s:
        StoreLedger(s).record(entry("lv_1"))
        StoreLedger(s).set_deadline("lv_1", later)
    with Session(engine) as s:
        found = StoreLedger(s).get("lv_1")
    engine.dispose()
    assert found is not None and found.deadline_at == later


def test_set_deadline_refuses_anything_but_a_pending_step_up(ledger: Ledger) -> None:
    later = DECIDED + timedelta(seconds=130)
    with pytest.raises(KeyError):
        ledger.set_deadline("lv_unknown", later)

    ledger.record(entry("lv_approved", outcome="approve"))
    with pytest.raises(ValueError, match="not awaiting an answer"):
        ledger.set_deadline("lv_approved", later)

    ledger.record(entry("lv_expired"))
    ledger.resolve("lv_expired", "decline", "timeout", DECIDED + timedelta(seconds=121),
                   message=expired_message(120))
    with pytest.raises(ValueError, match="not awaiting an answer"):
        ledger.set_deadline("lv_expired", later)
    resolved = ledger.get("lv_expired")
    assert resolved is not None and resolved.deadline_at is None
