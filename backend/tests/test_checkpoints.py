"""The checkpoint log (api-contract §3.10) and the required Viseca key.

The log describes a decision and never takes part in it: every public purchase gets
one, in engine order, naming every protection and warning sign it ran, and the
outcomes with the log on equal the outcomes with it off.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from oneguard import checkpoints
from oneguard.api.app import _default_client
from oneguard.api.models import Checkpoint
from oneguard.engine.explain import SIGNAL_LABELS
from oneguard.engine.ledger import StoreLedger
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex
from tests.test_api_contract import FakeViseca, fast, live_run, replay, running
from tests.test_oracle import ORACLE, to_policy

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
STAGE_ORDER = ["facts", "status", "rules", "model", "protections", "warnings", "signals", "decide", "explain", "record"]
PROTECTIONS = {SIGNAL_LABELS[f"A{n}"] for n in range(1, 8)}
WARNINGS = {SIGNAL_LABELS[f"W{n}"] for n in range(1, 7)}


@pytest.fixture(scope="module")
def pack() -> Pack:
    return Pack.load()


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> StoreHistoryIndex:
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('checkpoints') / 'ref.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        index = StoreHistoryIndex.load(s)
    engine.dispose()
    return index


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    yield engine
    engine.dispose()


def _run(pack: Pack, history: StoreHistoryIndex, db: Engine, scenario_id: str, *, log: bool) -> dict[str, tuple]:
    policy = to_policy(scenario_id)
    out = {}
    with Session(db, expire_on_commit=False) as s:
        ctx = PipelineContext(
            policy=policy, ledger=StoreLedger(s, history=history), history=history,
            run_id=f"cp-{scenario_id}-{log}", on_checkpoints=checkpoints.sink(db) if log else None,
        )  # fmt: skip
        for event in build_events(pack, scenario_id, mandate_id=policy.mandate_id, now=NOW):
            engine, _, decision = decide_event(event, ctx)
            auth = event["authorization"]
            out[auth["source_authorization_id"]] = (auth["authorization_id"], engine.outcome, decision)
    return out


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_every_purchase_gets_a_log_in_engine_order(pack, history, db, scenario_id):
    runs = _run(pack, history, db, scenario_id, log=True)
    with Session(db) as s:
        stored = checkpoints.load(s, (live for live, _, _ in runs.values()))
    for source_id, (live_id, outcome, decision) in runs.items():
        rows = stored.get(live_id)
        assert rows, source_id
        assert decision.checkpoints is None, "the log is read from the store, not returned by the pipeline"
        stages = [r.stage for r in rows]
        assert stages == sorted(stages, key=STAGE_ORDER.index), f"{source_id}: {stages}"
        assert stages[0] == "facts" and stages[-1] == "record"
        names = {r.check for r in rows}
        assert PROTECTIONS <= names, f"{source_id}: a protection is missing from the log"
        assert WARNINGS <= names, f"{source_id}: a warning sign is missing from the log"
        (decided,) = [r for r in rows if r.stage == "decide"]
        assert decided.check == {"approve": "Approve", "decline": "Decline", "step_up": "Ask you"}[outcome]
        assert all(r.ms is not None for r in rows if r.stage in ("facts", "decide", "explain", "record"))


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_the_log_never_changes_a_decision(pack, history, tmp_path, scenario_id):
    outcomes = []
    for log in (True, False):
        engine = make_engine(f"sqlite:///{tmp_path / f'{log}.sqlite'}")
        init_db(engine)
        outcomes.append({k: v[1] for k, v in _run(pack, history, engine, scenario_id, log=log).items()})
        engine.dispose()
    assert outcomes[0] == outcomes[1]


def test_a_redelivery_writes_no_second_log(pack, history, db):
    policy = to_policy("SCEN0000")
    (event,) = build_events(pack, "SCEN0000", mandate_id=policy.mandate_id, now=NOW)
    writes = []
    with Session(db, expire_on_commit=False) as s:
        ctx = PipelineContext(
            policy=policy, ledger=StoreLedger(s, history=history), history=history, run_id="redelivery",
            on_checkpoints=lambda live, rows, at: writes.append(live),
        )  # fmt: skip
        decide_event(event, ctx)
        decide_event(event, ctx)
    assert len(writes) == 1


def test_a_failing_sink_never_breaks_the_decision(pack, history, db):
    policy = to_policy("SCEN0000")
    (event,) = build_events(pack, "SCEN0000", mandate_id=policy.mandate_id, now=NOW)

    def broken(*_: object) -> None:
        raise RuntimeError("store down")

    with Session(db, expire_on_commit=False) as s:
        ctx = PipelineContext(
            policy=policy, ledger=StoreLedger(s, history=history), history=history, run_id="broken",
            on_checkpoints=broken,
        )  # fmt: skip
        engine, _, _ = decide_event(event, ctx)
    assert engine.outcome == "approve"


def test_save_is_once_per_purchase(db):
    first = [Checkpoint(stage="facts", check="Purchase read", outcome="done", detail="a", ms=1.0)]
    second = [Checkpoint(stage="facts", check="Purchase read", outcome="done", detail="b")]
    with Session(db) as s:
        checkpoints.save(s, "LIVE1", first, NOW)
        checkpoints.save(s, "LIVE1", second, NOW)
        assert checkpoints.load(s, ["LIVE1", "MISSING"]) == {"LIVE1": first}


def test_the_app_needs_the_viseca_key(monkeypatch, db):
    monkeypatch.delenv("VISECA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VISECA_API_KEY"):
        _default_client(db)
    monkeypatch.setenv("VISECA_API_KEY", "test-key")
    assert _default_client(db) is not None


# Through the HTTP API (C6), with the helpers of test_api_contract.py -------------------


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture
def db_url(seeded: Path, tmp_path: Path) -> str:
    path = tmp_path / "oneguard.sqlite"
    shutil.copy(seeded, path)
    return f"sqlite:///{path}"


def test_c6_decisions_carry_their_log(db_url):
    async def scenario() -> None:
        async with running(db_url) as run:
            (only,) = await replay(run, "SCEN0000", "CA0001", 1, "CU0001")
            stages = [c["stage"] for c in only["checkpoints"]]
            assert stages[0] == "facts" and stages[-1] == "record"
            assert only["latency_ms"] > 0

    asyncio.run(scenario())


def test_worker_decisions_carry_their_log(db_url):
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            decisions = await live_run(run, "SCEN0000", "CA0001", 1)
            (only,) = decisions
            stages = [c["stage"] for c in only["checkpoints"]]
            assert stages[0] == "facts" and stages[-1] == "record"

    asyncio.run(scenario())
