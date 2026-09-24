"""The Viseca worker against the in-process fake sandbox (tests/fake_viseca.py).

Unless a test says otherwise every engine function is stubbed, so every purchase is a
``step_up``. Deadlines and the human window are shortened to keep the suite fast; the
logic is the live one. Every test that uses ``db`` runs twice: on P2's ``StoreLedger``
(the worker's own ``default_ledger``) and on the ``InMemoryLedger`` reference.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select

from oneguard.api import models as api
from oneguard.engine import stubs
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.engine.tier3 import rewrite_explanation
from oneguard.engine.types import Policy
from oneguard.llm.provider import NullProvider, ProviderUnavailable
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import AuthorizationHistory, EventRaw, Run, WorkerState
from oneguard.viseca import worker as worker_module
from oneguard.viseca.client import VisecaClient, VisecaError, store_sink
from oneguard.viseca.worker import (
    TIER3_TIMEOUT_S,
    NotAwaitingAnswer,
    ScopedStoreLedger,
    VisecaWorker,
    WindowClosed,
    default_ledger,
    overrun_setting,
    timeout_message,
)
from tests.fake_viseca import FakeConfig, FakeViseca

REPO = Path(__file__).resolve().parents[2]
ALL_STUBS = {"implementations": stubs.STUBS, "stubbed": frozenset(stubs.STUBS)}


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("viseca") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture(scope="module")
def history(seeded_db: Path) -> StoreHistoryIndex:
    engine = make_engine(f"sqlite:///{seeded_db}")
    with session(engine) as s:
        index = StoreHistoryIndex.load(s)
    engine.dispose()
    return index


@pytest.fixture(params=["store", "memory"])
def ledger_kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """``store``: the worker builds its default ledger, a ``StoreLedger`` session on ``db``.
    ``memory``: the worker gets the ``InMemoryLedger`` reference instead."""
    if request.param == "memory":
        monkeypatch.setattr(
            worker_module, "default_ledger", lambda db, history: InMemoryLedger(history=history)
        )
    return request.param


@pytest.fixture
def db(seeded_db: Path, tmp_path: Path, ledger_kind: str) -> Engine:
    path = tmp_path / "oneguard.sqlite"
    shutil.copy(seeded_db, path)
    engine = make_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()


def fake_client(fake: FakeViseca, db: Engine, key: str | None = None) -> VisecaClient:
    return VisecaClient(
        "http://fake-viseca",
        key if key is not None else fake.config.api_key,
        transport=httpx.ASGITransport(app=fake.app),
        sink=store_sink(db),
    )


@asynccontextmanager
async def harness(
    db: Engine, config: FakeConfig, **worker_options: Any
) -> AsyncIterator[tuple[FakeViseca, VisecaClient, VisecaWorker]]:
    fake = FakeViseca(config)
    client = fake_client(fake, db)
    options = {**ALL_STUBS, "poll_wait_s": 0.2, "waiting_step_up_pause_s": 0.05, **worker_options}
    worker = VisecaWorker(client, db=db, **options)
    try:
        yield fake, client, worker
    finally:
        await worker.stop()
        await client.aclose()


async def start_run(
    client: VisecaClient,
    worker: VisecaWorker,
    scenario_id: str,
    hard_rules: list | None = None,
    uncertainty: str = "ask",
) -> tuple[str, str]:
    draft = await client.create_mandate("Test instruction.", hard_rules or [], uncertainty)
    mandate = await client.confirm_mandate(draft["draft_id"])
    run = await client.create_run(scenario_id, mandate["mandate_id"])
    worker.track_run(run["run_id"], scenario_id=scenario_id, viseca_mandate_id=mandate["mandate_id"])
    return mandate["mandate_id"], run["run_id"]


async def wait_until(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def timeout_resolution(auth: Any) -> dict[str, Any]:
    """The worker's one timeout ``/resolve`` for ``auth``, accepted by the platform.

    ``fast`` has the fake expire step-ups itself well after the worker's deadline, so the
    worker reads them as still pending and its ``/resolve`` lands; exactly one is sent.
    """
    (resolution,) = auth.resolutions
    assert "rejected" not in resolution and not auth.platform_expired
    assert auth.status == "declined"
    return resolution


def fast(**overrides: Any) -> FakeConfig:
    defaults = {
        "decision_deadline_s": 3.0,
        "human_window_s": 60.0,
        "max_wait_s": 0.2,
        "platform_expiry_offset_s": 5.0,
    }
    return FakeConfig(**{**defaults, **overrides})


def test_the_default_ledger_is_the_store_ledger_on_short_sessions(
    seeded_db: Path, history: StoreHistoryIndex
) -> None:
    engine = make_engine(f"sqlite:///{seeded_db}")
    try:
        ledger = default_ledger(engine, history)
        assert isinstance(ledger, ScopedStoreLedger) and ledger.history is history
        assert ledger.get("lv_unknown") is None  # a call outside a scope: its own session
        assert ledger.open_sessions == 0 and engine.pool.checkedout() == 0
        with ledger.scope() as inner:
            assert isinstance(inner, StoreLedger) and inner.history is history
            with ledger.scope() as nested:
                assert nested is inner
            assert ledger.get("lv_unknown") is None  # runs on the scope's session
            assert ledger.open_sessions == 1
        assert ledger.open_sessions == 0 and engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_timeout_message_is_the_documented_wording_for_the_default_window() -> None:
    documented = "No answer within 120 s; nothing was approved"
    assert timeout_message(120) == documented
    for doc in ("docs/rules.md", "docs/api-contract.md"):
        assert documented in (REPO / doc).read_text(encoding="utf-8")
    assert timeout_message(1.5) == "No answer within 1.5 s; nothing was approved"


@pytest.mark.parametrize(
    "engine_functions",
    [ALL_STUBS, {"implementations": None, "stubbed": None}],
    ids=["all-stubs", "registered-lanes"],
)
def test_all_45_events_are_decided_and_expire_unanswered(
    db: Engine, history: StoreHistoryIndex, engine_functions: dict[str, Any]
) -> None:
    """Every event decided and every step-up closed at its window.

    ``all-stubs`` pins the stub outcomes; ``registered-lanes`` runs whatever lanes have
    merged (stubs for the rest) and checks only what holds for any engine.
    """
    stubbed = engine_functions is ALL_STUBS

    async def scenario() -> None:
        config = fast(human_window_s=0.5)
        async with harness(db, config, history=history, **engine_functions) as (fake, client, worker):
            await worker.start()
            runs = {}
            for scenario_id in fake.pack.scenario_ids():
                runs[scenario_id] = (await start_run(client, worker, scenario_id))[1]

            def all_closed() -> bool:
                auths = fake.all_auths()
                return len(auths) == 45 and all(a.status in ("approved", "declined") for a in auths)

            await wait_until(all_closed, timeout=40)
            await wait_until(lambda: all(r.state == "done" for r in worker.status().runs))

            auths = fake.all_auths()
            assert not any(a.auto_declined for a in auths)
            for auth in auths:
                posted = auth.decisions[0]
                assert posted["customer_message"] and posted["evidence"] and posted["reason_codes"]
                assert posted["engine_version"].startswith("oneguard/")
                if stubbed:
                    assert posted["decision"] == "step_up" and posted["reason_codes"] == ["stub"]
                    assert "stubs=all" in posted["engine_version"]
                if posted["decision"] == "step_up":
                    resolution = timeout_resolution(auth)
                    assert resolution["decision"] == "decline"
                    assert resolution["customer_message"] == timeout_message(0.5)
                else:
                    assert not auth.resolutions

            entries = await worker.ledger_entries([a.live_id for a in auths])
            assert len(entries) == 45
            assert all(e.final and e.reserved_chf == 0.0 for e in entries)
            for entry in entries:
                if entry.outcome == "step_up":
                    assert (entry.uncertain_outcome, entry.resolved_by, entry.spent_chf) == (
                        "expired", "timeout", 0.0,
                    )  # fmt: skip
            # live → source map, and the rewritten related id points at the related live id
            assert {worker.source_ids[a.live_id] for a in auths} == {a.source_id for a in auths}
            related = {worker.source_ids[a]: worker.source_ids[b] for a, b in worker.related_ids.items()}
            assert related == {"AU0042": "AU0037"}

            await wait_until(lambda: worker.events_cursor == len(fake.feed))
            status = worker.status()
            assert status.ok and status.pending_step_ups == 0
            assert status.last_poll_at is not None
            assert {r.scenario_id: (r.delivered, r.decided, r.total) for r in status.runs} == {
                sid: (n, n, n) for sid, n in {"SCEN0000": 1, "SCEN0001": 10, "SCEN0002": 12,
                                              "SCEN0003": 11, "SCEN0004": 11}.items()
            }  # fmt: skip

        with session(db) as s:
            assert s.scalar(select(func.count()).select_from(EventRaw)) == 45
            stored = {r.viseca_run_id: r for r in s.scalars(select(Run))}
        assert {r.state for r in stored.values()} == {"done"}
        assert stored[runs["SCEN0001"]].decided == 10

    asyncio.run(scenario())


def test_a_redelivered_event_is_counted_once(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        config = fast(redeliver=frozenset({"AU0004"}))
        seen: list[api.Decision] = []
        async with harness(db, config, history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            await wait_until(lambda: len(seen) == 10)
            redelivered = fake.by_source(run_id, "AU0004")
            await wait_until(lambda: redelivered.deliveries == 2 and len(redelivered.decisions) == 2)

            # the second post repeats the stored decision; the platform already has it
            first, second = redelivered.decisions
            assert second["decision"] == first["decision"] and second.get("rejected")
            auths = fake.runs[run_id].auths
            entries = await worker.ledger_entries([a.live_id for a in auths])
            assert len(entries) == 10
            reserved = round(sum(e.reserved_chf for e in entries), 2)
            billed = round(sum(a.template["authorization"]["billing_amount_chf"] for a in auths), 2)
            assert reserved == billed
            run = worker.run_status(run_id)
            assert run is not None
            assert (run.delivered, run.decided, run.pending_human, run.redeliveries) == (10, 10, 10, 1)
            assert worker.status().pending_step_ups == 10

    asyncio.run(scenario())


def test_the_live_run_row_is_written_before_its_first_decision(
    db: Engine, history: StoreHistoryIndex
) -> None:
    """The ledger carries the session watch and remembered answers over from earlier live
    runs only for a run whose ``runs`` row says ``kind = live``; a run with no row is a replay."""

    async def scenario() -> None:
        rows_at_decision: list[tuple[str, str, str] | None] = []

        def check(decision: api.Decision) -> None:
            with session(db) as s:
                row = s.scalars(select(Run)).first()
                rows_at_decision.append(row and (row.kind, row.mandate_id, row.card_id))

        async with harness(db, fast(), history=history) as (_, client, worker):
            worker.add_listener(check)
            await worker.start()
            await start_run(client, worker, "SCEN0000")
            await wait_until(lambda: len(rows_at_decision) == 1)
        kind, mandate_id, card_id = rows_at_decision[0] or ("", "", "")
        assert kind == "live" and mandate_id and card_id

    asyncio.run(scenario())


def spy_on_feed(client: VisecaClient) -> list[tuple[int | str, dict[str, Any]]]:
    """Every ``GET /v1/events`` the worker makes through ``client``: (since, reply)."""
    calls: list[tuple[int | str, dict[str, Any]]] = []
    read = client.events

    async def events(since: int | str = 0) -> dict[str, Any]:
        reply = await read(since=since)
        calls.append((since, reply))
        return reply

    client.events = events  # type: ignore[method-assign]
    return calls


def stored_cursor(db: Engine) -> Any:
    with session(db) as s:
        row = s.get(WorkerState, worker_module.EVENTS_CURSOR_KEY)
        return row and row.value


def test_the_first_boot_reads_the_event_feed_from_0_and_stores_the_cursor(
    db: Engine, history: StoreHistoryIndex
) -> None:
    async def scenario() -> None:
        async with harness(db, fast(), history=history) as (fake, client, worker):
            feed = spy_on_feed(client)
            await worker.start()
            assert worker.events_cursor == 0 and stored_cursor(db) is None
            _, run_id = await start_run(client, worker, "SCEN0001")
            await wait_until(lambda: all(a.decisions for a in fake.runs[run_id].auths))
            await wait_until(lambda: stored_cursor(db) == len(fake.feed) >= 10)
            assert feed[0][0] == 0
            assert worker.events_cursor == stored_cursor(db)

    asyncio.run(scenario())


def test_a_restart_resumes_the_event_feed_from_the_stored_cursor(
    db: Engine, history: StoreHistoryIndex
) -> None:
    """The cursor is stored once a page is processed; a restarted worker starts there and
    never reads an earlier feed item again (the team-wide feed is not re-scanned)."""

    async def scenario() -> None:
        config = fast(feed_status_override={"AU0002": "approved"})
        async with harness(db, config, history=history) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: all(a.decisions for a in auths))
            await wait_until(lambda: stored_cursor(db) == len(fake.feed))
            await worker.stop()
            cursor = stored_cursor(db)
            assert isinstance(cursor, int) and cursor >= 10

            feed = spy_on_feed(client)
            options = {**ALL_STUBS, "poll_wait_s": 0.2, "waiting_step_up_pause_s": 0.05}
            restarted = VisecaWorker(client, db=db, history=history, **options)
            try:
                await restarted.start()
                assert restarted.events_cursor == cursor
                _, next_run = await start_run(client, restarted, "SCEN0000")
                await wait_until(lambda: all(a.decisions for a in fake.runs[next_run].auths))
                await wait_until(lambda: stored_cursor(db) == len(fake.feed) > cursor)
            finally:
                await restarted.stop()
            assert feed[0][0] == cursor
            assert all(since >= cursor for since, _ in feed)
            items = [item["event_id"] for _, reply in feed for item in reply["events"]]
            assert items and min(items) == cursor + 1

    asyncio.run(scenario())


def test_an_unanswered_step_up_is_declined_at_the_window(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        seen: list[api.Decision] = []
        async with harness(db, fast(human_window_s=1.0), history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.resolutions) and len(seen) == 2, timeout=10)

            assert auth.accepted_at is not None
            window_end = auth.accepted_at + timedelta(seconds=1.0)
            pending, closed = seen
            assert pending.status == "pending_human" and pending.deadline_at == window_end
            assert pending.deadline_at == auth.expires_at  # the reply's step_up_expires_at
            resolution = timeout_resolution(auth)
            assert resolution["at"] >= window_end
            assert resolution == {
                "decision": "decline",
                "customer_message": "No answer within 1 s; nothing was approved",
                "evidence": [
                    {"rule": "resolved_by", "outcome": "info", "detail": "timeout", "source": "ledger"}
                ],
                "at": resolution["at"],
            }
            (entry,) = await worker.ledger_entries([auth.live_id])
            assert entry.final and entry.uncertain_outcome == "expired" and entry.resolved_by == "timeout"
            assert entry.reserved_chf == 0.0 and entry.spent_chf == 0.0
            assert (closed.decision, closed.uncertain_outcome, closed.status) == ("uncertain", "expired", "final")
            assert closed.resolved_by == "timeout" and closed.deadline_at is None
            # served again on every poll while it waited: nothing more posted, nothing counted
            assert auth.step_up_serves > 0 and len(auth.decisions) == 1
            assert worker.run_status(run_id).redeliveries == 0  # type: ignore[union-attr]
            assert worker.status().last_error is None
            # the platform was read first, narrowed to the run
            assert fake.authorization_reads == [{"run_id": run_id, "status": None}]

    asyncio.run(scenario())


async def closed_by_the_window(
    fake: FakeViseca, worker: VisecaWorker, run_id: str, seen: list[api.Decision]
) -> tuple[Any, Any]:
    (auth,) = fake.runs[run_id].auths
    await wait_until(lambda: len(seen) == 2, timeout=10)
    (entry,) = await worker.ledger_entries([auth.live_id])
    assert (entry.final, entry.uncertain_outcome, entry.resolved_by) == (True, "expired", "timeout")
    assert entry.reserved_chf == 0.0 and entry.spent_chf == 0.0
    assert (seen[1].uncertain_outcome, seen[1].resolved_by) == ("expired", "timeout")
    assert auth.status == "declined" and auth.platform_expired
    assert worker.status().last_error is None and worker.status().pending_step_ups == 0
    return auth, entry


def test_a_step_up_the_platform_already_expired_is_recorded_without_a_resolve(
    db: Engine, history: StoreHistoryIndex
) -> None:
    async def scenario() -> None:
        seen: list[api.Decision] = []
        config = fast(human_window_s=1.0, platform_expiry_offset_s=-0.3)
        async with harness(db, config, history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            auth, _ = await closed_by_the_window(fake, worker, run_id, seen)
            assert auth.resolutions == []  # nothing posted
            assert fake.authorization_reads == [{"run_id": run_id, "status": None}]

    asyncio.run(scenario())


def test_a_step_up_the_platform_expires_between_read_and_resolve_gets_one_resolve(
    db: Engine, history: StoreHistoryIndex
) -> None:
    async def scenario() -> None:
        seen: list[api.Decision] = []
        config = fast(human_window_s=1.0, expire_before_resolve=frozenset({"AU0001"}))
        async with harness(db, config, history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            auth, _ = await closed_by_the_window(fake, worker, run_id, seen)
            (resolution,) = auth.resolutions  # the one /resolve, answered 409
            assert resolution["rejected"] and resolution["customer_message"] == timeout_message(1.0)
            # read before the /resolve, and again after its 409
            assert fake.authorization_reads == [{"run_id": run_id, "status": None}] * 2

    asyncio.run(scenario())


def test_a_refused_customer_answer_is_never_sent_again(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        seen: list[api.Decision] = []
        config = fast(human_window_s=1.0, expire_before_resolve=frozenset({"AU0001"}))
        async with harness(db, config, history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: len(seen) == 1)
            with pytest.raises(VisecaError) as refused:
                await worker.resolve_by_customer(auth.live_id, "approve")
            assert refused.value.code == "authorization_not_pending"
            with pytest.raises(NotAwaitingAnswer):
                await worker.resolve_by_customer(auth.live_id, "approve")
            # the window closes: the platform's result is recorded, nothing is sent again
            await closed_by_the_window(fake, worker, run_id, seen)
            (resolution,) = auth.resolutions
            assert resolution["rejected"] and resolution["decision"] == "approve"

    asyncio.run(scenario())


def test_a_stale_pending_step_up_envelope_never_sends_a_second_resolve(
    db: Engine, history: StoreHistoryIndex
) -> None:
    """The CI race: a ``pending_step_up`` envelope served just before the expiry arrives
    after the ledger closed the step-up; nothing may be resolved again."""

    async def scenario() -> None:
        config = fast(human_window_s=0.5, pending_serve_delay_s=0.2)
        async with harness(db, config, history=history) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: all(a.status == "declined" for a in auths), timeout=30)
            await asyncio.sleep(0.5)  # let the last stale envelopes arrive
            assert sum(a.step_up_serves for a in auths) > 0
            for auth in auths:
                assert timeout_resolution(auth)["customer_message"] == timeout_message(0.5)
            assert worker.status().last_error is None

    asyncio.run(scenario())


def test_the_ledger_holds_no_connection_between_decisions_or_after_stop(
    db: Engine, history: StoreHistoryIndex, ledger_kind: str
) -> None:
    async def scenario() -> None:
        async with harness(db, fast(), history=history) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: all(a.decisions for a in auths), timeout=15)
            # between decisions (the poll loop keeps reading the ledger) no session stays open
            await wait_until(lambda: db.pool.checkedout() == 0, timeout=5)
            if ledger_kind == "store":
                ledger = worker.ledger
                assert isinstance(ledger, ScopedStoreLedger)
                await wait_until(lambda: ledger.open_sessions == 0, timeout=5)
            await worker.stop()
            assert db.pool.checkedout() == 0
            if ledger_kind == "store":
                assert ledger.open_sessions == 0

    asyncio.run(scenario())


def test_the_worker_keeps_polling_while_step_ups_pend(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        seen: list[api.Decision] = []
        async with harness(db, fast(human_window_s=60.0), history=history) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: len(seen) == 10, timeout=15)

            assert {a.status for a in auths} == {"pending"}
            assert not any(a.resolutions or a.auto_declined for a in auths)
            entries = {e.live_authorization_id: e for e in await worker.ledger_entries([a.live_id for a in auths])}
            for auth in auths:
                assert auth.accepted_at is not None
                assert entries[auth.live_id].deadline_at == auth.accepted_at + timedelta(seconds=60)
            polls = fake.polls
            await asyncio.sleep(0.5)
            assert fake.polls > polls
            # the waiting step-ups come back on every poll; the worker neither re-posts nor spins
            assert sum(a.step_up_serves for a in auths) > 0
            assert all(len(a.decisions) == 1 for a in auths)
            assert fake.polls - polls <= 0.5 / 0.05 + 2
            status = worker.status()
            assert status.ok and status.pending_step_ups == 10

    asyncio.run(scenario())


def test_a_revoked_mandate_declines_everything_delivered_afterwards(
    db: Engine, history: StoreHistoryIndex
) -> None:
    async def scenario() -> None:
        async with harness(db, fast(), history=history) as (fake, client, worker):
            await worker.start()
            mandate_id, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: sum(bool(a.decisions) for a in auths) >= 2)
            before = datetime.now(UTC)
            await worker.revoke(mandate_id)
            after = datetime.now(UTC)
            assert fake.mandates[mandate_id]["status"] == "revoked"
            await wait_until(lambda: all(a.decisions for a in auths), timeout=15)

            entries = await worker.ledger_entries([a.live_id for a in auths])
            earlier = [e for e in entries if e.decided_at < before]
            later = [e for e in entries if e.decided_at > after]
            assert len(earlier) >= 2 and len(later) >= 5
            assert {e.outcome for e in earlier} == {"step_up"}
            for entry in later:
                assert entry.outcome == "decline" and entry.step == 1
                assert entry.reason_codes == ["card_or_authority_inactive"]
                assert entry.evidence[0].rule == "policy_status" and entry.evidence[0].outcome == "fail"
            posted_later = {a.live_id: a.decisions[0] for a in auths}
            for entry in later:
                posted = posted_later[entry.live_authorization_id]
                assert posted["decision"] == "decline"
                assert posted["reason_codes"] == ["card_or_authority_inactive"]

    asyncio.run(scenario())


def test_context_and_event_feed_mismatches_become_info_evidence(
    db: Engine, history: StoreHistoryIndex
) -> None:
    async def scenario() -> None:
        config = fast(context_spend_offset=5.0, feed_status_override={"AU0002": "approved"})
        async with harness(db, config, history=history) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            await wait_until(lambda: all(a.decisions for a in auths[:3]))

            for auth in auths[:3]:
                rows = [r for r in auth.decisions[0]["evidence"] if r["rule"] == "ledger_mismatch"]
                assert rows and all(r["outcome"] == "info" and r["source"] == "ledger" for r in rows)
                assert rows[0]["detail"] == (
                    "Viseca counts CHF 5.00 approved in this period; our ledger counts CHF 0.00."
                )
            feed_rows = [
                r["detail"]
                for auth in auths[1:3]
                for r in auth.decisions[0]["evidence"]
                if "event feed" in r["detail"]
            ]
            assert feed_rows == [
                f"Viseca's event feed shows {auths[0].live_id} as approved; our ledger has it as pending."
            ]

    asyncio.run(scenario())


def test_a_request_failing_the_event_schema_is_declined(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        async with harness(db, fast(corrupt=frozenset({"AU0001"})), history=history) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions))
            posted = auth.decisions[0]
            assert posted["decision"] == "decline" and posted["reason_codes"] == ["unevaluable"]
            assert posted["evidence"][0]["rule"] == "event_schema"
            assert await worker.ledger_entries([auth.live_id]) == []
            assert "schema" in (worker.status().last_error or "")

    asyncio.run(scenario())


@pytest.mark.parametrize("uncertainty", ["ask", "approve"])
def test_an_engine_over_budget_posts_a_step_up_before_the_deadline_that_expires_unanswered(
    db: Engine, history: StoreHistoryIndex, uncertainty: str
) -> None:
    """ask and approve both step up: approve is never automatic (D2, D3)."""

    def slow_facts(event: dict, index: Any) -> Any:
        time.sleep(1.5)
        return stubs.build_facts(event, index)

    async def scenario() -> None:
        functions = {**stubs.STUBS, "build_facts": slow_facts}
        config = fast(decision_deadline_s=1.2, human_window_s=2.0)
        async with harness(
            db, config, history=history, implementations=functions, budget_ms=100
        ) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000", uncertainty=uncertainty)
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions), timeout=5)
            assert not auth.auto_declined
            assert auth.accepted_at is not None and auth.accepted_at < auth.deadline_at
            (posted,) = auth.decisions
            assert posted["decision"] == "step_up" and posted["reason_codes"] == ["unevaluable"]
            assert posted["customer_message"] == "We couldn't check this purchase in time; please review it"
            assert posted["evidence"][0]["rule"] == "engine"

            # the late engine result stores the claimed step-up, with its reservation
            await wait_until(lambda: auth.live_id in worker._run_of[auth.live_id].pending)
            (entry,) = await worker.ledger_entries([auth.live_id])
            amount = auth.template["authorization"]["billing_amount_chf"]
            assert (entry.outcome, entry.final, entry.uncertain_outcome) == ("step_up", False, "pending")
            assert entry.reason_codes == ["unevaluable"] and entry.reserved_chf == amount
            assert entry.deadline_at == auth.accepted_at + timedelta(seconds=2.0)
            assert worker.status().pending_step_ups == 1

            # unanswered: the timeout decline at accepted time + human window; the ledger
            # records it once the platform exchange is over (the expiry task then ends)
            await wait_until(lambda: worker.status().pending_step_ups == 0, timeout=5)
            resolution = timeout_resolution(auth)
            assert resolution["decision"] == "decline"
            assert resolution["customer_message"] == timeout_message(2.0)
            assert resolution["evidence"][0]["detail"] == "timeout"
            assert resolution["at"] >= entry.deadline_at
            (entry,) = await worker.ledger_entries([auth.live_id])
            assert (entry.final, entry.uncertain_outcome, entry.resolved_by) == (True, "expired", "timeout")
            assert entry.reserved_chf == 0.0 and entry.spent_chf == 0.0
            assert len(auth.decisions) == 1

    asyncio.run(scenario())


def test_an_engine_over_budget_under_a_decline_setting_posts_a_final_decline(
    db: Engine, history: StoreHistoryIndex
) -> None:
    release = threading.Event()

    def slow_facts(event: dict, index: Any) -> Any:
        release.wait(5)
        return stubs.build_facts(event, index)

    async def scenario() -> None:
        functions = {**stubs.STUBS, "build_facts": slow_facts}
        config = fast(decision_deadline_s=1.2, human_window_s=2.0)
        async with harness(
            db, config, history=history, implementations=functions, budget_ms=100
        ) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000", uncertainty="decline")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions), timeout=5)
            assert not auth.auto_declined
            (posted,) = auth.decisions
            assert posted["decision"] == "decline" and posted["reason_codes"] == ["unevaluable"]
            assert posted["customer_message"] == (
                "Declined: we could not finish checking this purchase in time, so nothing was approved."
            )
            assert posted["evidence"][0]["rule"] == "engine"

            # the late engine result stores the claimed decline: final, nothing reserved
            release.set()
            deadline = time.monotonic() + 5
            while not (entries := await worker.ledger_entries([auth.live_id])):
                assert time.monotonic() < deadline, "the overrun decline was never recorded"
                await asyncio.sleep(0.02)
            (entry,) = entries
            assert (entry.outcome, entry.final, entry.uncertain_outcome) == ("decline", True, None)
            assert entry.reason_codes == ["unevaluable"]
            assert entry.reserved_chf == 0.0 and entry.spent_chf == 0.0
            assert entry.deadline_at is None
            assert worker.status().pending_step_ups == 0
            assert auth.live_id not in worker._expiry

            # no expiry fires: nothing more is posted after the human window
            await asyncio.sleep(2.5)
            assert not auth.resolutions and len(auth.decisions) == 1

    asyncio.run(scenario())


def test_an_overrun_treats_a_missing_or_unknown_uncertainty_setting_as_ask() -> None:
    policy = Policy(mandate_id="M1", status="active", instruction="", rules=[], uncertainty_policy="ask")
    for setting in (None, "maybe"):
        odd = Policy.model_construct(**{**dict(policy), "uncertainty_policy": setting})
        assert overrun_setting(odd) == "ask"
    for setting in ("ask", "decline", "approve"):
        assert overrun_setting(policy.model_copy(update={"uncertainty_policy": setting})) == setting


def test_the_customer_can_answer_an_overrun_step_up(db: Engine, history: StoreHistoryIndex) -> None:
    def slow_facts(event: dict, index: Any) -> Any:
        time.sleep(1.5)
        return stubs.build_facts(event, index)

    async def scenario() -> None:
        functions = {**stubs.STUBS, "build_facts": slow_facts}
        async with harness(
            db, fast(decision_deadline_s=1.2), history=history, implementations=functions, budget_ms=100
        ) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions), timeout=5)
            entry = await worker.resolve_by_customer(auth.live_id, "approve")
            assert (entry.uncertain_outcome, entry.resolved_by) == ("approved", "customer")
            assert entry.spent_chf == auth.template["authorization"]["billing_amount_chf"]
            assert auth.resolutions[0]["customer_message"] == "The customer confirmed this purchase."
            assert worker.status().pending_step_ups == 0
            assert len(auth.decisions) == 1

    asyncio.run(scenario())


def test_the_customer_answer_closes_the_step_up(db: Engine, history: StoreHistoryIndex) -> None:
    offset = timedelta(0)

    def clock() -> datetime:
        return datetime.now(UTC) + offset

    async def scenario() -> None:
        nonlocal offset
        async with harness(db, fast(), history=history, now=clock) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            first, second = fake.runs[run_id].auths[:2]
            await wait_until(lambda: bool(first.decisions and second.decisions))

            entry = await worker.resolve_by_customer(first.live_id, "approve")
            assert (entry.uncertain_outcome, entry.resolved_by) == ("approved", "customer")
            assert entry.spent_chf == first.template["authorization"]["billing_amount_chf"]
            assert first.resolutions[0]["customer_message"] == "The customer confirmed this purchase."
            with pytest.raises(NotAwaitingAnswer):
                await worker.resolve_by_customer(first.live_id, "decline")

            offset = timedelta(seconds=61)
            with pytest.raises(WindowClosed):
                await worker.resolve_by_customer(second.live_id, "approve")
            assert not second.resolutions

    asyncio.run(scenario())


def test_start_reseeds_history_when_viseca_serves_a_different_file(
    db: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    lines = (REPO / "data" / "authorization_history.csv").read_text(encoding="utf-8").splitlines(True)
    changed = "".join(lines[:-5])

    async def scenario(config: FakeConfig) -> VisecaWorker:
        async with harness(db, config) as (_, _, worker):
            await worker.start()
            return worker

    worker = asyncio.run(scenario(fast()))
    assert not worker.history_reseeded
    # no hash is served: the file was downloaded and matched the pack
    assert worker.served_history_sha256 == seed_module.pack_file_sha256("authorization_history.csv")
    # timeouts are read from bootstrap ``limits``
    assert (worker.human_window_s, worker.decision_deadline_s) == (60.0, 3.0)
    with session(db) as s:
        assert s.scalar(select(func.count()).select_from(AuthorizationHistory)) == 4701

    caplog.set_level("WARNING")
    worker = asyncio.run(scenario(fast(history_csv=changed)))
    assert worker.history_reseeded
    assert "RE-SEEDED authorization_history: 4696 rows" in caplog.text
    with session(db) as s:
        assert s.scalar(select(func.count()).select_from(AuthorizationHistory)) == 4696


# Tier 3 ------------------------------------------------------------------------------------
#
# The worker schedules a decision's tier-3 task in the same event-loop step as it notifies
# the listeners, so once a listener has seen a decision its task is in ``worker._rewrites``.
# The tests await those tasks rather than a clock.

REWRITE = "We need your OK before this purchase goes through."
WITH_TIER3 = {
    "implementations": {**stubs.STUBS, "rewrite_explanation": rewrite_explanation},
    "stubbed": frozenset(stubs.STUBS) - {"rewrite_explanation"},
}


class SlowRewriter:
    """A provider that answers tier 3 only once released, recording every call."""

    def __init__(self, fail: bool = False) -> None:
        self.release = threading.Event()
        self.fail = fail
        self.calls: list[float] = []

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        self.calls.append(timeout_s)
        self.release.wait(30)
        if self.fail:
            raise ProviderUnavailable("timed out")
        return {"message": REWRITE}


async def messages(worker: VisecaWorker, live_ids: list[str]) -> set[tuple[str, str]]:
    return {(e.message, e.explanation_source) for e in await worker.ledger_entries(live_ids)}


def test_tier3_rewrites_the_posted_message_later_without_holding_up_decisions(
    db: Engine, history: StoreHistoryIndex
) -> None:
    """Every decision posts with its template while the model is still writing; the ledger
    (what C6 reads) then shows the rewrite with ``explanation_source: model``."""

    async def scenario() -> None:
        provider = SlowRewriter()
        seen: list[api.Decision] = []
        async with harness(db, fast(), history=history, provider=provider, **WITH_TIER3) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            auths = fake.runs[run_id].auths
            live_ids = [a.live_id for a in auths]
            # all ten decided and posted with the template while no rewrite can finish
            await wait_until(lambda: len(seen) == 10)
            tasks = list(worker._rewrites)
            assert len(tasks) == 10 and not any(t.done() for t in tasks)
            assert len(provider.calls) <= worker_module.TIER3_THREADS
            assert [a.decisions[0]["customer_message"] for a in auths] == ["stub"] * 10
            assert await messages(worker, live_ids) == {("stub", "template")}

            provider.release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 60)

            assert await messages(worker, live_ids) == {(REWRITE, "model")}
            assert provider.calls == [TIER3_TIMEOUT_S] * 10
            # listeners get the rewritten decision too; nothing was posted again
            latest = {d.authorization_id: d for d in seen}
            assert {(d.message, d.explanation_source) for d in latest.values()} == {(REWRITE, "model")}
            assert all(len(a.decisions) == 1 for a in auths)
            (entry,) = await worker.ledger_entries([live_ids[0]])
            assert (entry.outcome, entry.uncertain_outcome, entry.reason_codes) == ("step_up", "pending", ["stub"])

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", [None, NullProvider()], ids=["none", "null"])
def test_tier3_is_not_scheduled_without_a_provider(
    db: Engine, history: StoreHistoryIndex, provider: Any
) -> None:
    calls: list[str] = []

    def spy(*args: Any, **kwargs: Any) -> str:
        calls.append("called")
        return REWRITE

    async def scenario() -> None:
        functions = {**stubs.STUBS, "rewrite_explanation": spy}
        seen: list[api.Decision] = []
        async with harness(
            db, fast(), history=history, provider=provider, implementations=functions
        ) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            await wait_until(lambda: len(seen) == 10)
            assert not worker._rewrites
            live_ids = [a.live_id for a in fake.runs[run_id].auths]
            assert await messages(worker, live_ids) == {("stub", "template")}
        assert not calls

    asyncio.run(scenario())


def test_a_tier3_provider_failure_leaves_the_template(db: Engine, history: StoreHistoryIndex) -> None:
    async def scenario() -> None:
        provider = SlowRewriter(fail=True)
        seen: list[api.Decision] = []
        async with harness(db, fast(), history=history, provider=provider, **WITH_TIER3) as (fake, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0001")
            await wait_until(lambda: len(seen) == 10)
            tasks = list(worker._rewrites)
            provider.release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 60)  # the ones not already finished
            assert not worker._rewrites and provider.calls == [TIER3_TIMEOUT_S] * 10
            live_ids = [a.live_id for a in fake.runs[run_id].auths]
            assert await messages(worker, live_ids) == {("stub", "template")}
            assert len(seen) == 10  # no rewritten decision was announced

    asyncio.run(scenario())


def test_stop_stores_what_each_run_last_reported(
    db: Engine, history: StoreHistoryIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stop`` can cut the loop between a decision and its ``runs`` write (here the event
    feed is slow); the row a restarted process reads (D4, D7) still counts the decision."""
    sync_events = VisecaWorker._sync_events

    async def slow_feed(self: VisecaWorker) -> None:
        await asyncio.sleep(0.5)
        await sync_events(self)

    monkeypatch.setattr(VisecaWorker, "_sync_events", slow_feed)

    async def scenario() -> str:
        seen: list[api.Decision] = []
        async with harness(db, fast(), history=history) as (_, client, worker):
            worker.add_listener(seen.append)
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000")
            await wait_until(lambda: len(seen) == 1)
            status = worker.run_status(run_id)
            assert status is not None and status.decided == 1
        return run_id

    run_id = asyncio.run(scenario())
    with session(db) as s:
        row = s.scalars(select(Run).where(Run.viseca_run_id == run_id)).one()
        assert (row.delivered, row.decided) == (1, 1)
