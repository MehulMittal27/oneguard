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
from oneguard.engine.types import Policy
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import AuthorizationHistory, EventRaw, Run
from oneguard.viseca import worker as worker_module
from oneguard.viseca.client import VisecaClient, store_sink
from oneguard.viseca.worker import (
    NotAwaitingAnswer,
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
    options = {**ALL_STUBS, "poll_wait_s": 0.2, **worker_options}
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


def fast(**overrides: Any) -> FakeConfig:
    return FakeConfig(**{"decision_deadline_s": 3.0, "human_window_s": 60.0, "max_wait_s": 0.2, **overrides})


def test_the_default_ledger_is_the_store_ledger(seeded_db: Path, history: StoreHistoryIndex) -> None:
    engine = make_engine(f"sqlite:///{seeded_db}")
    try:
        ledger = default_ledger(engine, history)
        assert isinstance(ledger, StoreLedger) and ledger.history is history
        assert ledger.get("lv_unknown") is None
        ledger.session.close()
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
                    (resolution,) = auth.resolutions
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
            (resolution,) = auth.resolutions
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

            # unanswered: the timeout decline at accepted time + human window
            await wait_until(lambda: bool(auth.resolutions), timeout=5)
            (resolution,) = auth.resolutions
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
    with session(db) as s:
        assert s.scalar(select(func.count()).select_from(AuthorizationHistory)) == 4701

    caplog.set_level("WARNING")
    worker = asyncio.run(scenario(fast(history_csv=changed)))
    assert worker.history_reseeded
    assert "RE-SEEDED authorization_history: 4696 rows" in caplog.text
    with session(db) as s:
        assert s.scalar(select(func.count()).select_from(AuthorizationHistory)) == 4696
