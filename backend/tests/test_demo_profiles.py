"""Per-scenario profiles: which customer and card each served scenario runs on, and
``make demo-live`` driving the server that decides instead of deciding itself.

The served catalogue names no card. The platform names it in the bootstrap ``profile``
(one scenario), in every run's ``fixture_profiles`` and in every authorization; the worker
stores each sighting (``scenario_profiles``), and C12, D3 and D8 read them. The fake
sandbox here serves two scenarios only, as the judging sandbox does: SCEN9001 on the
bootstrap profile (CU9001, CA9001) and SCEN9002 on CU9002's card CA9002, which nothing
names before its first run.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import delete

from oneguard.store.db import make_engine, session
from oneguard.store.schema import Run, ScenarioProfile
from oneguard.viseca import demo
from oneguard.viseca import worker as worker_module
from oneguard.viseca.worker import ServedProfile, profile_sightings, served_scenario_ids
from tests.fake_viseca import JUDGING_EXTRA, FakeViseca, judging_pack
from tests.test_api_contract import (  # noqa: F401  fixtures
    Running,
    confirm_form,
    db_url,
    fast,
    running,
    seeded_db,
    until,
)

API = "http://oneguard.test"
REAL_ENGINE = {"implementations": None, "stubbed": None}
"""demo-live compiles the scenario's instruction, so the real (rule-based fallback) compiler."""

# Shapes as the live sandbox served them on 24 Sep 2026 (ids as served; trimmed).
LIVE_BOOTSTRAP = {
    "profile": {
        "profile_id": "PROFILE_AUTH0101",
        "scenario_id": "SCEN0101",
        "profile_context": {
            "profile_id": "PROFILE_AUTH0101",
            "customer_id": "CU1217",
            "account_id": "AC1275",
            "card_id": "CA1331",
        },
        "customer": {"customer_id": "CU1217", "persona_name": "Omar Chen"},
        "card": {"card_id": "CA1331", "account_id": "AC1275"},
    },
    "scenarios": [
        {"scenario_id": "SCEN0101", "scenario_name": "Connection check", "event_count": 2},
        {"scenario_id": "SCEN0106", "scenario_name": "Session integrity", "event_count": 12},
    ],
}
LIVE_FEED_COMPLETED = {
    "event_id": 6,
    "type": "scenario.completed",
    "run_id": "run_570532557042887f",
    "authorization_id": None,
    "data": {
        "run_id": "run_570532557042887f",
        "scenario_id": "SCEN0101",
        "fixture_profiles": [{"profile_id": "PROFILE_AUTH0101", "customer_id": "CU1217", "card_id": "CA1331"}],
    },
}
LIVE_AUTHORIZATION = {
    "authorization_id": "AU10601-45c27595",
    "scenario_id": "SCEN0106",
    "run_id": "run_45c275957418623e",
    "status": "pending_step_up",
    "authorization": {
        "authorization_id": "AU10601-45c27595",
        "scenario_id": "SCEN0106",
        "profile_id": "PROFILE_AUTH0106",
        "card_id": "CA1576",
    },
}


def test_the_platform_names_a_scenarios_card_in_three_places() -> None:
    omar = ServedProfile("SCEN0101", "CU1217", "CA1331", "PROFILE_AUTH0101")
    assert profile_sightings(LIVE_BOOTSTRAP) == [omar]
    assert profile_sightings([LIVE_FEED_COMPLETED]) == [omar]
    assert profile_sightings([LIVE_AUTHORIZATION]) == [ServedProfile("SCEN0106", None, "CA1576", "PROFILE_AUTH0106")]
    assert profile_sightings({"scenarios": LIVE_BOOTSTRAP["scenarios"]}) == []  # the catalogue names no card


def test_the_served_scenarios_come_from_bootstrap_else_reference_data() -> None:
    assert served_scenario_ids(LIVE_BOOTSTRAP, None) == ["SCEN0101", "SCEN0106"]
    reference = {"runtime": {"scenario_ids": ["SCEN0130", "SCEN0101"]}}
    assert served_scenario_ids({"scenarios": []}, reference) == ["SCEN0101", "SCEN0130"]
    assert served_scenario_ids(None, {"tables": {"scenario_catalogue": [{"scenario_id": "SCEN0117"}]}}) == ["SCEN0117"]
    assert served_scenario_ids(None, None) is None


def two_profiles() -> FakeViseca:
    """A sandbox serving SCEN9001 (bootstrap: CU9001, CA9001) and SCEN9002 (runs on CU9002's
    CA9002), both replaying SCEN0000's purchase, and none of the local pack's scenarios."""
    config = judging_pack()
    extra = copy.deepcopy(JUDGING_EXTRA)
    customer, account, card = extra["customers"][0], extra["accounts"][0], extra["cards"][0]
    extra["customers"].append({**customer, "customer_id": "CU9002", "persona_name": "Second Served"})
    extra["accounts"].append({**account, "account_id": "AC9002", "customer_id": "CU9002"})
    extra["cards"].append({**card, "card_id": "CA9002", "account_id": "AC9002"})
    extra["scenario_catalogue"].append(
        {
            "scenario_id": "SCEN9002",
            "scenario_name": "Served second profile",
            "cardholder_instruction": "Buy one ordinary grocery item for CHF 20 or less. Ask me when uncertain.",
            "event_count": 1,
        }
    )
    config["served_extra"] = extra
    config["served_scenarios"]["SCEN9002"] = "SCEN0000"
    config["fixture_profiles"]["SCEN9002"] = {"profile_id": "PROFILE_TEST9002", "customer_id": "CU9002", "card_id": "CA9002"}
    return FakeViseca(fast(serve_pack=False, human_window_s=0.5, **config))


async def customers(run: Running) -> dict[str, dict[str, Any]]:
    r = await run.get("/api/customers")
    assert r.status_code == 200, r.text
    return {c["customer_id"]: c for c in r.json()["customers"]}


async def scenarios(run: Running) -> dict[str, dict[str, Any]]:
    r = await run.get("/api/dev/scenarios")
    assert r.status_code == 200, r.text
    return {s["scenario_id"]: s for s in r.json()["scenarios"]}


async def run_done(run: Running, run_id: str) -> bool:
    return (await run.get(f"/api/dev/runs/{run_id}")).json()["state"] == "done"


def test_each_served_scenario_binds_its_own_customer_and_only_served_ones_are_live(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = two_profiles()
        async with running(db_url, fake=fake) as run:
            found = await customers(run)
            assert found["CU9001"] == {
                "customer_id": "CU9001",
                "name": "Test Served",
                "home_region": "Bern region",
                "card_id": "CA9001",
                "scenario_ids": ["SCEN9001"],
                "live": True,
            }
            assert (found["CU9002"]["scenario_ids"], found["CU9002"]["live"]) == ([], False)  # not run yet
            # the local pack still binds its scenarios, but the sandbox serves none of them
            assert (found["CU0001"]["scenario_ids"], found["CU0001"]["live"]) == (["SCEN0000", "SCEN0001"], False)
            assert [c for c, v in found.items() if v["live"]] == ["CU9001"]

            listed = await scenarios(run)
            assert listed["SCEN9001"]["served"] and listed["SCEN9001"]["profile"] == {
                "customer_id": "CU9001",
                "name": "Test Served",
                "card_id": "CA9001",
                "profile_id": "PROFILE_TEST9001",
                "source": "bootstrap",
            }
            assert listed["SCEN9002"]["served"] and listed["SCEN9002"]["profile"] is None
            assert not listed["SCEN0000"]["served"] and listed["SCEN0000"]["profile"]["source"] == "pack"

            # SCEN9002's card is unknown: its policy goes on the bootstrap card, as demo-live does
            await confirm_form(run, "CA9001")
            started = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9002", "card_id": "CA9001"})
            assert started.status_code == 200, started.text
            live = started.json()
            # the run's fixture profile names the card; the reply names its holder
            assert (live["card_id"], live["customer_id"], live["customer_name"]) == ("CA9002", "CU9002", "Second Served")
            # one run at a time
            again = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001"})
            assert again.status_code == 409 and again.json()["error"]["code"] == "run_active", again.text

            # the policy moved to the card the platform runs it on
            moved = (await run.get("/api/cards/CA9002/policy")).json()["mandate"]
            assert moved["status"] == "active" and moved["mandate_id"] == live["mandate_id"]
            left = (await run.get("/api/cards/CA9001/policy")).json()["mandate"]
            assert left["status"] == "revoked" and left["instruction"] == moved["instruction"]

            found = await customers(run)
            assert (found["CU9002"]["scenario_ids"], found["CU9002"]["live"], found["CU9002"]["card_id"]) == (
                ["SCEN9002"],
                True,
                "CA9002",
            )
            assert found["CU9001"]["scenario_ids"] == ["SCEN9001"]  # still its own, not SCEN9002
            assert (await scenarios(run))["SCEN9002"]["profile"]["source"] == "run"
            await until(lambda: run_done(run, live["run_id"]), timeout=20)
            decisions = await run.decisions("CU9002")
            assert decisions and all(d["card_id"] == "CA9002" for d in decisions)

        # a store that lost the binding learns it again from the platform's authorizations at start
        engine = make_engine(db_url)
        with session(engine) as s:
            s.execute(delete(ScenarioProfile).where(ScenarioProfile.scenario_id == "SCEN9002"))
        engine.dispose()
        async with running(db_url, fake=fake) as run:
            profile = (await scenarios(run))["SCEN9002"]["profile"]
            assert (profile["customer_id"], profile["card_id"], profile["source"]) == ("CU9002", "CA9002", "authorization")

    asyncio.run(scenario())


def test_d9_lists_every_scenario_grouped_by_customer_with_its_card(db_url: str) -> None:  # noqa: F811
    """D9 (the operator console's picker): the pack's scenarios and the served ones, each with
    its purchase count and the customer and card it runs on; unnamed ones last."""

    async def scenario() -> None:
        async with running(db_url, fake=two_profiles()) as run:
            r = await run.get("/api/scenarios")
            assert r.status_code == 200, r.text
            listed = r.json()["scenarios"]
            by_id = {s["scenario_id"]: s for s in listed}
            assert by_id["SCEN9001"] == {
                "scenario_id": "SCEN9001",
                "name": by_id["SCEN9001"]["name"],
                "event_count": by_id["SCEN9001"]["event_count"],
                "instruction": by_id["SCEN9001"]["instruction"],
                "customer_id": "CU9001",
                "customer_name": "Test Served",
                "card_id": "CA9001",
            }
            assert (by_id["SCEN0004"]["customer_id"], by_id["SCEN0004"]["card_id"]) == ("CU0019", "CA0039")
            assert by_id["SCEN0004"]["customer_name"] == "Oliver Graf" and by_id["SCEN0004"]["event_count"] == 11
            d8 = await scenarios(run)
            assert by_id["SCEN0004"]["instruction"] == d8["SCEN0004"]["cardholder_instruction"]
            assert set(by_id) == set(d8)
            # grouped: one customer's scenarios are consecutive; SCEN9002 (no card yet) is last
            assert listed[-1]["scenario_id"] == "SCEN9002" and listed[-1]["customer_id"] is None
            order = [s["customer_id"] for s in listed]
            assert all(order.index(c) + order.count(c) - 1 == len(order) - 1 - order[::-1].index(c) for c in order)
            assert by_id["SCEN0000"]["customer_id"] == by_id["SCEN0001"]["customer_id"] == "CU0001"

    asyncio.run(scenario())


@pytest.fixture
def no_local_worker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fails any worker demo-live would build, and counts every worker built meanwhile."""
    built: list[str] = []
    real_init = worker_module.VisecaWorker.__init__

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        built.append("worker")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(worker_module.VisecaWorker, "__init__", counting_init)
    return built


async def demo_live(run: Running, scenario_id: str, lines: list[str], **kw: Any) -> int:
    return await demo.live(
        scenario_id, api_base=API, transport=httpx.ASGITransport(app=run.app), out=lines.append, max_seconds=30, **kw
    )


def test_demo_live_starts_through_the_server_and_prints_whom_to_sign_in_as(
    db_url: str,  # noqa: F811
    no_local_worker: list[str],
) -> None:
    async def scenario() -> None:
        fake = two_profiles()
        lines: list[str] = []
        async with running(db_url, fake=fake, **REAL_ENGINE) as run:
            polls = fake.polls
            no_local_worker.clear()  # the server's own worker is built; demo-live builds none
            assert await demo_live(run, "SCEN9001", lines) == 0
            assert no_local_worker == []
            assert fake.polls > polls  # the server's worker kept polling: it decided the run

            assert lines[0] == f"OneGuard at {API} decides this run; this terminal only starts and follows it."
            sign_in = lines.index("Sign in as Test Served (CU9001, card CA9001)")
            first_decision = next(i for i, line in enumerate(lines) if line.startswith("  AU0001-"))
            assert sign_in < first_decision
            assert lines[sign_in + 1].startswith("Run run_") and lines[sign_in + 1].endswith(f" started at {API}")
            assert any(line.startswith("Instruction: Buy one ordinary grocery item") for line in lines)
            assert any(line.startswith("Policy md_") and line.endswith("confirmed on card CA9001") for line in lines)
            assert lines[-1].startswith("Summary: ")

            # SCEN9002 has never run: the policy starts on the bootstrap card and follows the run
            lines.clear()
            assert await demo_live(run, "SCEN9002", lines) == 0
            assert no_local_worker == []
            assert any("SCEN9002 has not run yet" in line for line in lines)
            assert "Sign in as Second Served (CU9002, card CA9002)" in lines

    asyncio.run(scenario())


def test_follow_prints_this_runs_decisions_even_when_decided_before_its_first_read() -> None:
    """The server's worker can decide the first purchase before ``follow`` reads anything;
    the customer's decisions from other runs are still left out."""

    def decision(live_id: str, run_id: str) -> dict[str, Any]:
        return {"authorization_id": live_id, "run_id": run_id, "decision": "approved", "status": "final",
                "billing_amount_chf": 20.0, "reason_codes": ["within_limits"], "message": "Approved."}

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dev/runs/run_1":
            return httpx.Response(200, json={"run_id": "run_1", "state": "done", "decided": 1,
                                             "pending_human": 0, "total": 1})
        assert request.url.path == "/api/customers/CU1/decisions"
        return httpx.Response(200, json={"decisions": [decision("AU0001-1", "live-run_1"),
                                                       decision("AU0001-0", "live-run_0")]})

    async def scenario() -> list[str]:
        lines: list[str] = []
        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(reply)) as http:
            assert await demo.follow(http, "run_1", "CU1", out=lines.append, max_seconds=5) == 0
        return lines

    lines = asyncio.run(scenario())
    assert [line.split()[0] for line in lines if line.startswith("  AU")] == ["AU0001-1"]
    assert lines[-1] == "Summary: {'approved': 1}"


def test_demo_live_changes_nothing_while_a_run_is_active(
    db_url: str,  # noqa: F811
    no_local_worker: list[str],
) -> None:
    async def scenario() -> None:
        fake = two_profiles()
        fake.config.human_window_s = 60.0  # the run's step-up keeps it running
        lines: list[str] = []
        async with running(db_url, fake=fake, **REAL_ENGINE) as run:
            await confirm_form(run, "CA9001")
            started = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001"})
            assert started.status_code == 200, started.text
            mandates = dict(fake.mandates)
            no_local_worker.clear()
            assert await demo_live(run, "SCEN9002", lines) == 1
            assert fake.mandates == mandates and len(fake.runs) == 1  # no new policy, no new run
            assert any(line.startswith(f"Run {started.json()['run_id']} (SCEN9001) is still running") for line in lines)
            assert not any(line.startswith("Sign in as") for line in lines)
            assert no_local_worker == []

    asyncio.run(scenario())


NO_SERVER = "http://127.0.0.1:9"
"""Nothing listens there."""


@pytest.mark.parametrize("offline", [False, True], ids=["demo-live", "demo-offline"])
def test_without_a_server_nothing_starts(
    offline: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_local_worker: list[str],
) -> None:
    """No OneGuard answers at ONEGUARD_API_URL: exit 1, say so, start no run and no worker."""
    monkeypatch.setenv("VISECA_API_KEY", "some-key")  # a key does not bring a local decider back
    monkeypatch.setenv(demo.API_ENV, NO_SERVER)
    sent: list[str] = []
    real_call = demo._call

    async def spy(http: httpx.AsyncClient, method: str, path: str, body: Any = None) -> Any:
        sent.append(f"{method} {path}")
        return await real_call(http, method, path, body)

    monkeypatch.setattr(demo, "_call", spy)
    assert demo.main(["--scenario", "SCEN0101", *(["--offline"] if offline else [])]) == 1
    out = capsys.readouterr().out
    assert f"No OneGuard server answers at {NO_SERVER}/healthz, so nothing was started" in out
    assert f"export {demo.API_ENV}=<server url>" in out
    assert sent == [] and no_local_worker == []


def test_a_non_oneguard_answer_counts_as_no_server(no_local_worker: list[str]) -> None:
    def elsewhere(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"}) if request.url.path == "/healthz" else pytest.fail(
            f"{request.method} {request.url.path} sent to a server that is not OneGuard"
        )

    lines: list[str] = []
    code = asyncio.run(
        demo.live("SCEN0101", api_base=API, transport=httpx.MockTransport(elsewhere), out=lines.append)
    )
    assert code == 1 and lines[0].startswith(f"No OneGuard server answers at {API}/healthz")


def test_the_server_defaults_to_the_cloud_app(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def live(scenario_id: str, *, api_base: str, **_: Any) -> int:
        seen.append(api_base)
        return 0

    monkeypatch.setattr(demo, "live", live)
    monkeypatch.delenv(demo.API_ENV, raising=False)
    assert demo.main(["--scenario", "SCEN0101"]) == 0
    monkeypatch.setenv(demo.API_ENV, "http://localhost:8000/")
    assert demo.main(["--scenario", "SCEN0101"]) == 0
    assert seen == ["https://oneguard.fly.dev", "http://localhost:8000"]


def test_demo_offline_restarts_the_servers_replay(db_url: str, no_local_worker: list[str]) -> None:  # noqa: F811
    async def scenario() -> None:
        lines: list[str] = []
        async with running(db_url) as run:
            await confirm_form(run, "CA0001")
            code = await demo.offline(
                "SCEN0000", api_base=API, card_id="CA0001", speed_ms=0,
                transport=httpx.ASGITransport(app=run.app), out=lines.append,
            )  # fmt: skip
            assert code == 0, lines
            assert lines == [f"Replay of SCEN0000 on card CA0001 started at {API}: 1 purchase, 0 ms apart."]
            assert (await run.get("/api/dev/replay")).json()["scenario_id"] == "SCEN0000"

            # no --card (`make demo-offline SCEN=…`): the scenario's own card, from D9
            lines.clear()
            code = await demo.offline(
                "SCEN0001", api_base=API, speed_ms=0, transport=httpx.ASGITransport(app=run.app), out=lines.append
            )
            assert code == 0, lines
            assert lines == [f"Replay of SCEN0001 on card CA0001 started at {API}: 10 purchases, 0 ms apart."]
        assert no_local_worker == []

    asyncio.run(scenario())


def test_demo_live_needs_a_server_connected_to_the_platform(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        lines: list[str] = []
        async with running(db_url) as run:  # no platform: the server has no worker
            assert await demo_live(run, "SCEN0000", lines) == 1
        assert lines == [
            (
                f"OneGuard at {API} is not connected to the payment platform (no worker), so it cannot run a "
                "scenario; nothing was started."
            )
        ]

    asyncio.run(scenario())


async def someone_elses_run(run: Running, fake: FakeViseca, scenario_id: str) -> str:
    """A run of ``scenario_id`` the server does not follow (its worker is stopped first), its
    first purchase still awaiting a decision at the platform."""
    await run.services.worker.stop()
    client = run.services.client
    draft = await client.create_mandate("Another decider's policy.", [], "ask")
    mandate = await client.confirm_mandate(draft["draft_id"])
    other = (await client.create_run(scenario_id, mandate["mandate_id"]))["run_id"]
    assert [a.status for a in fake.runs[other].auths] == ["queued"]
    return other


def test_d3_refuses_a_scenario_with_a_run_in_progress_unless_forced(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = two_profiles()
        fake.config.decision_deadline_s = 60.0  # the other run's purchase stays open
        async with running(db_url, fake=fake) as run:
            other = await someone_elses_run(run, fake, "SCEN9001")
            await confirm_form(run, "CA9001")

            refused = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001"})
            assert refused.status_code == 409, refused.text
            error = refused.json()["error"]
            assert error["code"] == "run_active" and error["detail"] == {"run_id": other, "scenario_id": "SCEN9001"}
            assert other in error["message"] and "force: true" in error["message"]
            assert len(fake.runs) == 1  # nothing started

            listed = await scenarios(run)
            assert (listed["SCEN9001"]["active_run_id"], listed["SCEN9002"]["active_run_id"]) == (other, None)

            forced = await run.post(
                "/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001", "force": True}
            )
            assert forced.status_code == 200, forced.text
            assert len(fake.runs) == 2 and forced.json()["run_id"] != other

    asyncio.run(scenario())


def test_a_run_the_store_saw_running_counts_until_the_platform_says_it_is_over(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = two_profiles()
        async with running(db_url, fake=fake) as run:
            engine = make_engine(db_url)
            with session(engine) as s:
                for run_id, state in (("run_unknown", "running"), ("run_old", "done")):
                    s.add(
                        Run(
                            run_id=f"live-{run_id}",
                            viseca_run_id=run_id,
                            kind="live",
                            scenario_id="SCEN9002" if state == "running" else "SCEN9001",
                            mandate_id="md_x",
                            card_id="CA9002",
                            state=state,
                            started_at=datetime.now(UTC),
                        )
                    )
            engine.dispose()
            listed = await scenarios(run)
            # the platform cannot tell (404): the store's word stands
            assert (listed["SCEN9002"]["active_run_id"], listed["SCEN9001"]["active_run_id"]) == ("run_unknown", None)
            refused = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9002", "card_id": "CA9001"})
            assert refused.status_code == 409 and refused.json()["error"]["detail"]["run_id"] == "run_unknown"

    asyncio.run(scenario())


def test_demo_live_names_the_run_in_progress_and_starts_another_only_with_force(
    db_url: str,  # noqa: F811
    no_local_worker: list[str],
) -> None:
    async def scenario() -> None:
        fake = two_profiles()
        fake.config.decision_deadline_s = 60.0
        async with running(db_url, fake=fake, **REAL_ENGINE) as run:
            other = await someone_elses_run(run, fake, "SCEN9001")
            no_local_worker.clear()
            lines: list[str] = []
            assert await demo_live(run, "SCEN9001", lines) == 1
            assert lines[-1] == (
                f"SCEN9001 already has run {other} in progress (running, or purchases still open at the "
                "platform). Nothing was changed; pass --force to start another anyway."
            )
            assert fake.mandates.keys() == {fake.runs[other].mandate["mandate_id"]} and len(fake.runs) == 1

            lines.clear()
            await demo.live(
                "SCEN9001",
                api_base=API,
                transport=httpx.ASGITransport(app=run.app),
                out=lines.append,
                force=True,
                max_seconds=0.5,  # nobody here decides it (the worker is stopped)
            )
            assert "Sign in as Test Served (CU9001, card CA9001)" in lines
            assert len(fake.runs) == 2 and no_local_worker == []

    asyncio.run(scenario())


def test_a_run_started_after_a_pack_change_compiles_its_new_instruction(
    db_url: str,  # noqa: F811
    no_local_worker: list[str],
) -> None:
    """A judge serves a new pack (new ``pack_version``, a changed instruction) while the
    server runs: D8 re-reads the bootstrap, the worker syncs the catalogue first, and
    demo-live compiles and confirms the new instruction."""
    new = "Buy one loaf of bread for CHF 10 or less. Ask me when uncertain."

    async def scenario() -> None:
        fake = two_profiles()
        lines: list[str] = []
        async with running(db_url, fake=fake, **REAL_ENGINE) as run:
            assert await demo_live(run, "SCEN9001", lines) == 0
            assert "Instruction: Buy one ordinary grocery item for CHF 20 or less. Ask me when uncertain." in lines

            fake.config.pack_version = "saw27"
            fake.config.served_extra["scenario_catalogue"][0]["cardholder_instruction"] = new
            lines.clear()
            assert await demo_live(run, "SCEN9001", lines) == 0
            assert f"Instruction: {new}" in lines
            latest = max(fake.mandates.values(), key=lambda m: m["created_at"])
            assert latest["instruction"] == new
            assert run.services.worker.pack_version == "saw27"

    asyncio.run(scenario())
