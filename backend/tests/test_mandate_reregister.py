"""D3 when the platform no longer has the policy's mandate active, and the refusals kept in
``worker_events`` (docs/api-contract.md §1.2, docs/database.md §2).

The sandbox keeps one active mandate per team: a policy confirmed later (another card, a
later judging run) supersedes ours at the platform while the customer's policy stays
active here. D3 reads the mandate first and, when it is superseded or missing, registers
the same policy again and starts the run under the new platform mandate; the local policy
keeps its id and status, and its passport names the new platform reference. A mandate
superseded while its run is delivering is logged once and the run decides on under the
local policy. Every platform refusal is kept (the newest ``KEEP``) and ``/healthz`` shows
the last one, so a refusal is never lost with the log buffer.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select

from oneguard.passport.signer import DeviceKey
from oneguard.store import worker_events
from oneguard.store.db import make_engine, session
from oneguard.store.schema import Mandate, Run, WorkerEvent
from oneguard.viseca import demo
from oneguard.viseca.client import Refusal, VisecaClient, VisecaError
from tests.fake_viseca import FakeViseca
from tests.test_api_contract import (  # noqa: F401  fixtures
    Running,
    confirm_form,
    db_url,
    fast,
    live_run,
    running,
    seeded_db,
    until,
)

SECRET = "sk-team-DO-NOT-LEAK-5b1e0d2a"


def stored_mandate(run: Running, mandate_id: str) -> Mandate:
    with session(run.services.db_engine) as s:
        row = s.get(Mandate, mandate_id)
        assert row is not None
        return row


def events(run: Running, kind: str) -> list[WorkerEvent]:
    return worker_events.latest(run.services.db_engine, kind, limit=worker_events.KEEP)  # type: ignore[arg-type]


async def start(run: Running, scenario_id: str = "SCEN0000", card_id: str = "CA0001") -> httpx.Response:
    return await run.post("/api/dev/runs", json={"scenario_id": scenario_id, "card_id": card_id})


async def decided(run: Running, run_id: str, n: int) -> None:
    async def done() -> bool:
        return (await run.get(f"/api/dev/runs/{run_id}")).json()["decided"] >= n

    await until(done)


@pytest.mark.parametrize("how", ["superseded", "missing"])
def test_d3_registers_the_policy_again_when_the_platform_mandate_is_not_active(db_url: str, how: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            policy = await confirm_form(run)
            (old,) = run.fake.mandates
            first = (await run.get("/api/cards/CA0001/passport")).json()
            assert first["document"]["platform_mandate_id"] == old
            if how == "superseded":  # a later confirmation elsewhere replaced it at the platform
                run.fake.mandates[old]["status"] = "superseded"
            else:  # the platform no longer knows it (a team reset)
                run.fake.mandates.pop(old)

            r = await start(run)
            assert r.status_code == 200, r.text
            live = r.json()
            new = live["platform_mandate"]["viseca_mandate_id"]
            assert new != old and run.fake.mandates[new]["status"] == "active"
            assert live["platform_mandate"] == {
                "status_before": how,
                "reregistered": True,
                "viseca_mandate_id": new,
                "previous_viseca_mandate_id": old,
            }
            (fake_run,) = run.fake.runs.values()
            assert fake_run.mandate["mandate_id"] == new  # the run started under the new mandate
            same = ("instruction", "hard_rules", "uncertainty_policy", "guidance")
            registered = run.fake.mandates[new]
            assert {k: registered[k] for k in same} == {k: fake_run.mandate[k] for k in same}
            assert registered["hard_rules"] and registered["guidance"]

            # the local policy keeps its id and stays active; only the platform reference changed
            assert live["mandate_id"] == policy["mandate_id"]
            card = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (card["mandate_id"], card["status"], card["checks"]) == (
                policy["mandate_id"], "active", policy["checks"],
            )  # fmt: skip
            assert stored_mandate(run, policy["mandate_id"]).viseca_mandate_id == new

            # the passport is re-issued naming the new platform mandate
            passport = (await run.get("/api/cards/CA0001/passport")).json()
            assert passport["passport_id"] == first["passport_id"]
            assert passport["version"] == first["version"] + 1
            assert passport["document"]["platform_mandate_id"] == new
            assert passport["document"]["checks"] == first["document"]["checks"]
            assert [v["reason"] for v in passport["versions"]][-1] == "platform"

            # the run decides under the local policy, and D4 / D7 keep the evidence
            await decided(run, live["run_id"], 1)
            (decision,) = await run.decisions()
            assert "no_active_policy" not in decision["reason_codes"]
            assert (await run.get(f"/api/dev/runs/{live['run_id']}")).json()["platform_mandate"] == live["platform_mandate"]
            assert (await run.get("/api/dev/runs/current")).json()["platform_mandate"] == live["platform_mandate"]
            (noted,) = events(run, "mandate_reregistered")
            assert (noted.mandate_id, noted.code) == (new, how) and old in (noted.message or "")

            # a second run under the now active mandate registers nothing again
            await until(lambda: run.services.worker.run_status(live["run_id"]).state == "done")
            again = await start(run)
            assert again.status_code == 200, again.text
            assert again.json()["platform_mandate"] == {
                "status_before": "active", "reregistered": False, "viseca_mandate_id": new,
            }  # fmt: skip
            assert len(run.fake.mandates) == (2 if how == "superseded" else 1)

        # after a restart D4 reads the evidence from the stored run
        async with running(db_url, fake=FakeViseca(fast())) as run:
            with session(run.services.db_engine) as s:
                row = s.scalar(select(Run).where(Run.viseca_run_id == live["run_id"]))
                assert row is not None and row.platform_mandate == live["platform_mandate"]

    asyncio.run(scenario())


def test_an_active_platform_mandate_starts_the_run_as_before(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            await confirm_form(run)
            (tm,) = run.fake.mandates
            r = await start(run)
            assert r.status_code == 200, r.text
            assert r.json()["platform_mandate"] == {"status_before": "active", "reregistered": False, "viseca_mandate_id": tm}
            assert list(run.fake.mandates) == [tm] and not events(run, "mandate_reregistered")
            passport = (await run.get("/api/cards/CA0001/passport")).json()
            assert passport["version"] == 1

            # an unreadable mandate is not a reason to register again: the run's own answer decides
            await until(lambda: run.services.worker.run_status(r.json()["run_id"]).state == "done")
            run.faulty.fail[("GET", "/v1/mandates")] = 503
            r = await start(run)
            assert r.status_code == 200, r.text
            assert r.json()["platform_mandate"] == {"status_before": None, "reregistered": False, "viseca_mandate_id": tm}

    asyncio.run(scenario())


def test_a_refused_reregistration_starts_nothing_and_the_refusal_is_kept(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            assert (await run.get("/healthz")).json()["last_refusal"] is None
            policy = await confirm_form(run)
            (old,) = run.fake.mandates
            run.fake.mandates[old]["status"] = "superseded"
            run.faulty.fail[("POST", "/v1/mandates")] = 503

            r = await start(run)
            assert r.status_code == 503
            error = r.json()["error"]
            assert error["code"] == "upstream_unavailable"
            assert error["detail"] == {"platform_status": 503, "platform_code": "unavailable"}
            assert not run.fake.runs and list(run.fake.mandates) == [old]
            row = stored_mandate(run, policy["mandate_id"])
            assert (row.status, row.viseca_mandate_id) == ("active", old)

            await until(lambda: events(run, "platform_refusal"))
            refusal = events(run, "platform_refusal")[0]
            assert (refusal.action, refusal.status, refusal.code) == ("create_mandate", 503, "unavailable")
            assert "injected" in (refusal.message or "")
            health = (await run.get("/healthz")).json()
            last = health["last_refusal"]
            assert (last["action"], last["status"], last["code"]) == ("create_mandate", 503, "unavailable")
            assert last["at"].endswith("Z")

            # the operator retries once the platform answers: registered again, run started
            run.faulty.fail.clear()
            r = await start(run)
            assert r.status_code == 200, r.text
            assert r.json()["platform_mandate"]["reregistered"] is True

            # the run's own refusal is kept too, naming the mandate it was about
            await until(lambda: run.services.worker.run_status(r.json()["run_id"]).state == "done")
            new = stored_mandate(run, policy["mandate_id"]).viseca_mandate_id
            run.faulty.fail[("POST", "/v1/scenario-runs")] = 409
            r = await start(run)
            assert r.status_code == 503 and r.json()["error"]["detail"]["platform_status"] == 409
            await until(lambda: events(run, "platform_refusal")[0].action == "create_run")
            assert events(run, "platform_refusal")[0].mandate_id == new
            assert (await run.get("/healthz")).json()["last_refusal"]["action"] == "create_run"

    asyncio.run(scenario())


def test_a_mandate_superseded_mid_run_is_noted_once_and_the_run_decides_on(
    db_url: str,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            run.fake.hold()  # the run starts, its first purchase waits until released
            policy = await confirm_form(run)
            (tm,) = run.fake.mandates
            r = await start(run, "SCEN0001")
            assert r.status_code == 200, r.text
            run.fake.mandates[tm]["status"] = "superseded"  # a later judging run's policy replaced it
            with caplog.at_level(logging.WARNING, logger="oneguard.viseca.worker"):
                run.fake.release()
                decisions = await until(lambda: _all(run, 10))
            assert all("no_active_policy" not in d["reason_codes"] for d in decisions)
            assert {d["policy_applied"]["mandate_id"] for d in decisions if d.get("policy_applied")} <= {policy["mandate_id"]}
            noted = [m for m in caplog.messages if "reports mandate" in m]
            assert noted == [
                (
                    f"the platform reports mandate {tm} superseded during run {r.json()['run_id']}; "
                    f"deciding on under the local policy {policy['mandate_id']} (active)"
                )
            ]
            await until(lambda: events(run, "mandate_inactive"))
            (event,) = events(run, "mandate_inactive")
            assert (event.mandate_id, event.code, event.run_id) == (tm, "superseded", r.json()["run_id"])
            card = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (card["mandate_id"], card["status"]) == (policy["mandate_id"], "active")

    asyncio.run(scenario())


async def _all(run: Running, n: int) -> list[dict[str, Any]] | None:
    listed = await run.decisions()
    return listed if len(listed) == n else None


def test_the_same_run_with_its_mandate_superseded_decides_as_with_it_active(db_url: str, tmp_path: Path) -> None:  # noqa: F811
    """Superseded at the platform changes no outcome: the local policy decides."""
    copy = tmp_path / "control.sqlite"
    shutil.copy(db_url.removeprefix("sqlite:///"), copy)

    async def outcomes(url: str, supersede: bool) -> list[tuple[str, str, str | None]]:
        async with running(url, fake=FakeViseca(fast())) as run:
            if supersede:
                run.fake.hold()
                await confirm_form(run)
                (tm,) = run.fake.mandates
                assert (await start(run, "SCEN0001")).status_code == 200
                run.fake.mandates[tm]["status"] = "superseded"
                run.fake.release()
                listed = await until(lambda: _all(run, 10))
            else:
                listed = await live_run(run)
            return sorted((run.fake.auths[d["authorization_id"]].source_id, d["decision"], d["status"]) for d in listed)

    async def scenario() -> None:
        assert await outcomes(db_url, True) == await outcomes(f"sqlite:///{copy}", False)

    asyncio.run(scenario())


def test_demo_live_goes_through_d3_and_says_when_it_registered_again(db_url: str) -> None:  # noqa: F811
    """demo-live confirms a fresh policy (C2), then D3; a confirmation elsewhere in between
    supersedes it at the platform, and D3 registers it again."""

    async def scenario() -> None:
        fake = FakeViseca(fast())
        lines: list[str] = []
        async with running(db_url, fake=fake, implementations=None, stubbed=None) as run:
            app = httpx.ASGITransport(app=run.app)

            class SupersedeBeforeD3(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                    if request.method == "POST" and request.url.path == "/api/dev/runs":
                        for mandate in fake.mandates.values():
                            mandate["status"] = "superseded"
                    return await app.handle_async_request(request)

            code = await demo.live(
                "SCEN0000", api_base="http://oneguard.test", transport=SupersedeBeforeD3(), out=lines.append,
                max_seconds=30, device=DeviceKey(), card_id="CA0001",
            )  # fmt: skip
            assert code == 0, lines
            (note,) = [line for line in lines if line.startswith("Platform mandate re-registered")]
            (old, new) = sorted(fake.mandates, key=lambda tm: fake.mandates[tm]["status"] == "active")
            assert note == f"Platform mandate re-registered: {old} was superseded, the policy now runs as {new}"
            (fake_run,) = fake.runs.values()
            assert fake_run.mandate["mandate_id"] == new

    asyncio.run(scenario())


# worker_events ----------------------------------------------------------------------------


@pytest.fixture
def store(seeded_db: Path, tmp_path: Path) -> Engine:  # noqa: F811
    path = tmp_path / "events.sqlite"
    shutil.copy(seeded_db, path)
    engine = make_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()


def test_only_the_newest_200_of_each_kind_are_kept(store: Engine) -> None:
    at = datetime(2026, 9, 25, 9, 45, tzinfo=UTC)
    worker_events.record(store, worker_events.Event(at=at, kind="mandate_inactive", code="superseded"))
    for i in range(worker_events.KEEP + 5):
        worker_events.record(
            store,
            worker_events.Event(
                at=at + timedelta(seconds=i), kind="platform_refusal", action="create_run", status=409, code=f"c{i}",
            ),
        )  # fmt: skip
    with session(store) as s:
        kept = list(s.scalars(select(WorkerEvent.code).where(WorkerEvent.kind == "platform_refusal").order_by(WorkerEvent.id)))
        other = s.scalar(select(func.count()).select_from(WorkerEvent).where(WorkerEvent.kind == "mandate_inactive"))
    assert kept == [f"c{i}" for i in range(5, worker_events.KEEP + 5)]
    assert other == 1  # another kind is pruned on its own
    (newest,) = worker_events.latest(store, "platform_refusal")
    assert newest.code == f"c{worker_events.KEEP + 4}"


def test_a_long_message_is_cut_and_nullable_fields_stay_null(store: Engine) -> None:
    at = datetime(2026, 9, 25, 9, 45, tzinfo=UTC)
    worker_events.record(store, worker_events.Event(at=at, kind="platform_refusal", code="upstream_unavailable", message="x" * 5000))
    (row,) = worker_events.latest(store, "platform_refusal")
    assert len(row.message or "") == worker_events.MESSAGE_LIMIT and (row.message or "").endswith("more chars]")
    assert (row.status, row.action, row.mandate_id, row.run_id, row.authorization_id) == (None, None, None, None, None)


def test_a_refusal_names_the_call_and_its_body_and_never_the_key(store: Engine) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "mandate_inactive", "message": "mandate is not active", "details": [SECRET]}}
        return httpx.Response(409, json=body)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no route to {SECRET}", request=request)

    seen: list[Refusal] = []

    async def scenario() -> None:
        for transport in (refuse, unreachable):
            client = VisecaClient("http://x", SECRET, transport=httpx.MockTransport(transport))
            client.refusal_sink = lambda r: (seen.append(r), worker_events.refusal_sink(store)(r))
            async with client:
                with pytest.raises(VisecaError):
                    await client.create_run("SCEN0136", "TM848a490987be8f79")

    asyncio.run(scenario())
    refused, network = seen
    assert (refused.action, refused.status, refused.code, refused.mandate_id) == (
        "create_run", 409, "mandate_inactive", "TM848a490987be8f79",
    )  # fmt: skip
    assert '"mandate is not active"' in refused.message and "[redacted]" in refused.message
    assert (network.status, network.code) == (None, "upstream_unavailable")
    rows = worker_events.latest(store, "platform_refusal", limit=10)
    assert len(rows) == 2
    for row in rows:
        assert SECRET not in repr([getattr(row, c.name) for c in row.__table__.columns])
