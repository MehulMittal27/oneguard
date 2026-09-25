"""replay/runner.py records a replay like a live run: a ``runs`` row with kind = replay (M18).

`make replay` goes through the offline replay D2 uses, so each replay writes its runs row
before its first decision, its events to ``events_raw`` and its decisions to
``decisions``. Remembered answers and the session watch never cross between a replay and
a live run (docs/decisions.md M18), in either direction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from oneguard.engine import interfaces
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import EvidenceRow
from oneguard.replay import runner
from oneguard.replay.events import Pack
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import Decision, EventRaw, Run

POLICIES = Path(__file__).parent / "fixtures" / "policies"


@pytest.fixture(scope="module")
def pack() -> Pack:
    return Pack.load()


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("runner") / "seeded.sqlite"
    db = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=db)
    db.dispose()
    return path


@pytest.fixture
def db(seeded: Path, tmp_path: Path) -> Engine:
    copy = tmp_path / "store.sqlite"
    copy.write_bytes(seeded.read_bytes())
    engine = make_engine(f"sqlite:///{copy}")
    yield engine
    engine.dispose()


def policy(scenario_id: str):
    return runner.load_policy(POLICIES / f"{scenario_id}.yaml", mandate_id=f"TM_REPLAY_{scenario_id}")


def card_of(pack: Pack, scenario_id: str) -> tuple[str, str]:
    authority = pack.authorities[pack.attempts_for(scenario_id)[0]["authority_id"]]
    return authority["card_id"], authority["customer_id"]


def outcomes(replay: runner.Replay) -> list[tuple[str, str, list[str]]]:
    return [(d.source_id, d.outcome, d.reason_codes) for d in replay.decided]


def frozen_for_a_new_live_run(db: Engine, card_id: str, customer_id: str, mandate_id: str) -> bool:
    """Would a live session starting now on this card begin under the session watch?"""
    with Session(db, expire_on_commit=False) as s:
        s.add(Run(run_id="live-next", kind="live", mandate_id=mandate_id, card_id=card_id,
                  state="running", started_at=datetime.now(UTC) + timedelta(hours=1)))  # fmt: skip
        s.commit()
        ledger = StoreLedger(s, history=StoreHistoryIndex.load(s))
        at = datetime(2026, 9, 1, tzinfo=UTC)
        return ledger.view(run_id="live-next", customer_id=customer_id, card_id=card_id, at=at, period_days=7).frozen


def test_a_replay_is_recorded_as_a_replay_run(pack, db):
    replay = runner.decide_all(pack, "SCEN0004", policy("SCEN0004"), signals=False, db=db)
    card_id, _ = card_of(pack, "SCEN0004")
    asks = sum(d.outcome == "step_up" for d in replay.decided)
    with session(db) as s:
        run = s.get(Run, replay.run_id)
        assert (run.kind, run.state, run.scenario_id, run.mandate_id, run.card_id) == (
            "replay", "done", "SCEN0004", "TM_REPLAY_SCEN0004", card_id,
        )  # fmt: skip
        assert (run.delivered, run.decided, run.pending_human, run.total) == (11, 11, asks, 11)
        assert run.viseca_run_id is None and run.finished_at is not None and run.last_error is None
        stored = s.scalars(select(Decision).where(Decision.run_id == replay.run_id).order_by(Decision.ts_sim)).all()
        assert [(d.outcome, d.reason_codes, d.message) for d in stored] == [
            (d.outcome, d.reason_codes, d.message) for d in replay.decided
        ]
        events = s.scalar(select(func.count()).select_from(EventRaw).where(EventRaw.run_id == replay.run_id))
        assert events == 11
    assert [d.source_id for d in replay.decided] == [a["authorization_id"] for a in pack.attempts_for("SCEN0004")]


def test_the_runs_row_is_written_before_the_first_decision(pack, db, monkeypatch):
    """M18: the ledger reads runs.kind while deciding, so the row must already be there."""
    real = interfaces.load_implementations()
    kinds: list[str | None] = []

    def decide(*args, **kwargs):
        with session(db) as s:
            kinds.append(s.scalar(select(Run.kind).where(Run.scenario_id == "SCEN0001")))
        return real["decide"](*args, **kwargs)

    monkeypatch.setattr(runner, "load_implementations", lambda: {**real, "decide": decide})
    runner.decide_all(pack, "SCEN0001", policy("SCEN0001"), signals=False, db=db)
    assert kinds == ["replay"] * 10


def test_step_ups_stay_pending(pack, db):
    """Nobody answers a replay's step-up (CLAUDE.md rule 6): not the runner, not a timeout."""
    replay = runner.decide_all(pack, "SCEN0003", policy("SCEN0003"), signals=False, db=db)
    with session(db) as s:
        asks = s.scalars(select(Decision).where(Decision.run_id == replay.run_id, Decision.outcome == "step_up")).all()
        assert asks and all(not d.final and d.uncertain_outcome == "pending" and d.resolved_by is None for d in asks)


def test_replaying_twice_into_one_store_decides_the_same(pack, db):
    """Fresh live ids per replay (no redelivery), and nothing carried from the first."""
    throwaway = runner.decide_all(pack, "SCEN0003", policy("SCEN0003"), signals=False)
    first = runner.decide_all(pack, "SCEN0003", policy("SCEN0003"), signals=False, db=db)
    second = runner.decide_all(pack, "SCEN0003", policy("SCEN0003"), signals=False, db=db)
    assert first.run_id != second.run_id
    assert outcomes(first) == outcomes(second) == outcomes(throwaway)
    with session(db) as s:
        assert s.scalar(select(func.count()).select_from(Decision)) == 22


def test_a_live_session_watch_never_carries_into_a_replay(pack, db):
    card_id, customer_id = card_of(pack, "SCEN0001")
    mandate_id = "TM_REPLAY_SCEN0001"
    with Session(db, expire_on_commit=False) as s:
        s.add(Run(run_id="live-1", kind="live", mandate_id=mandate_id, card_id=card_id,
                  state="done", started_at=datetime.now(UTC) - timedelta(days=1)))  # fmt: skip
        s.commit()
        StoreLedger(s, history=StoreHistoryIndex.load(s)).record(LedgerEntry(
            live_authorization_id="live-watch", run_id="live-1", mandate_id=mandate_id, card_id=card_id,
            customer_id=customer_id, ts_sim=datetime(2026, 8, 1, tzinfo=UTC), outcome="step_up", final=False,
            uncertain_outcome="pending", merchant_id="M1", item_ids=["I1"], billing_amount_chf=5.0,
            session_trust="frozen", step=6, deciding_ids=["W1", "W2"], reason_codes=["session_watch"],
            evidence=[EvidenceRow(rule="W1", outcome="fail", detail="d", source="history")],
            message="m", engine_version="test", latency_ms=1.0, signals_enabled=False,
            decided_at=datetime.now(UTC) - timedelta(days=1),
        ))  # fmt: skip
    assert frozen_for_a_new_live_run(db, card_id, customer_id, mandate_id)  # the watch is really on
    replay = runner.decide_all(pack, "SCEN0001", policy("SCEN0001"), signals=False, db=db)
    assert outcomes(replay) == outcomes(runner.decide_all(pack, "SCEN0001", policy("SCEN0001"), signals=False))


@pytest.mark.parametrize("kind", ["replay", "live"])
def test_a_replay_never_teaches_a_live_session(pack, db, kind):
    """SCEN0003 ends under the session watch; only a live run would hand it on (the control)."""
    card_id, customer_id = card_of(pack, "SCEN0003")
    replay = runner.decide_all(pack, "SCEN0003", policy("SCEN0003"), signals=False, db=db)
    with session(db) as s:
        s.get(Run, replay.run_id).kind = kind
    assert frozen_for_a_new_live_run(db, card_id, customer_id, "TM_REPLAY_SCEN0003") is (kind == "live")
