"""A sandbox serving a superset of ``data/`` (the judging pack): the app accepts its ids.

The worker syncs the served reference tables at start; then C12 lists the served
customer with the bootstrap profile's card and scenario, C10 and C1 work on the
served-only card, D3 starts a run of a served-only scenario, and D2 replays that
scenario from record: the live run's stored events through the current engine, with no
platform call ("not run yet" before any live run of it).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import Engine, func, select

from oneguard.store.db import session
from oneguard.store.schema import EventRaw, Run, VisecaCall
from tests.fake_viseca import FakeViseca, judging_pack
from tests.test_api_contract import (  # noqa: F401  fixtures
    Running,
    confirm_form,
    db_url,
    fast,
    running,
    seeded_db,
    until,
)


def test_served_customers_cards_and_scenarios_are_accepted(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast(**judging_pack()))
        async with running(db_url, fake=fake) as run:
            customers = {c["customer_id"]: c for c in (await run.get("/api/customers")).json()["customers"]}
            assert customers["CU9001"] == {
                "customer_id": "CU9001",
                "name": "Test Served",
                "home_region": "Bern region",
                "card_id": "CA9001",
                "scenario_ids": ["SCEN9001"],
                "live": True,
            }
            assert customers["CU0001"]["scenario_ids"] == ["SCEN0000", "SCEN0001"]  # the local pack still binds

            accounts = (await run.get("/api/customers/CU9001/accounts")).json()["accounts"]
            assert [(a["account_id"], [c["card_id"] for c in a["cards"]]) for a in accounts] == [("AC9001", ["CA9001"])]

            mandate = await confirm_form(run, "CA9001")  # C1 + C2 on a served-only card
            assert mandate["card_id"] == "CA9001" and mandate["status"] == "active"

            wrong_card = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA0001"})
            assert wrong_card.status_code == 422, wrong_card.text
            unknown = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9999", "card_id": "CA9001"})
            assert unknown.status_code == 404, unknown.text

            summaries = {s["scenario_id"]: s for s in (await run.get("/api/scenarios")).json()["scenarios"]}
            assert summaries["SCEN9001"]["replay_source"] is None
            assert summaries["SCEN0000"]["replay_source"] == "pack"
            not_yet = await run.post(
                "/api/dev/replay/restart", json={"scenario_id": "SCEN9001", "card_id": "CA9001", "speed_ms": 0}
            )
            assert not_yet.status_code == 404, not_yet.text
            assert "not run yet" in not_yet.json()["error"]["message"]

            started = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001"})
            assert started.status_code == 200, started.text
            assert started.json()["scenario_id"] == "SCEN9001"
            assert [r.scenario_id for r in fake.runs.values()] == ["SCEN9001"]
            await until(lambda: run.services.worker.live_run(started.json()["run_id"]).decided == 1)

            live_id = f"live-{started.json()['run_id']}"
            await until(lambda: run.services.worker.live_run(started.json()["run_id"]).state == "done")
            await replay_from_record(run, fake, started.json()["run_id"], live_id)

    asyncio.run(scenario())


def _posts(db: Engine) -> int:
    with session(db) as s:
        return s.scalar(select(func.count()).select_from(VisecaCall).where(VisecaCall.method != "GET")) or 0


async def replay_from_record(run: Running, fake: FakeViseca, platform_run_id: str, live_id: str) -> None:
    """D2 on a served scenario replays its live run's stored events locally: the same
    purchases under fresh ids, marked as a replay from record, and nothing sent to the
    platform."""
    db = run.services.db_engine
    summaries = {s["scenario_id"]: s for s in (await run.get("/api/scenarios")).json()["scenarios"]}
    assert summaries["SCEN9001"]["replay_source"] == "record"
    wrong = await run.post(
        "/api/dev/replay/restart", json={"scenario_id": "SCEN9001", "card_id": "CA0001", "speed_ms": 0}
    )
    assert wrong.status_code == 422, wrong.text

    posts, platform = _posts(db), [(len(a.decisions), len(a.resolutions)) for a in fake.all_auths()]
    replay = await run.post(
        "/api/dev/replay/restart", json={"scenario_id": "SCEN9001", "card_id": "CA9001", "speed_ms": 0}
    )
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["source"] == "record" and body["record_run_id"] == platform_run_id
    assert body["policy_source"] == "card"  # CA9001's confirmed policy decides, as for a pack replay
    assert (body["customer_id"], body["card_id"], body["total"]) == ("CU9001", "CA9001", 1)
    ledger_run = body["ledger_run_id"]
    await until(lambda: run.services.offline.status().decided == 1)

    current = (await run.get("/api/dev/runs/current")).json()
    assert current["ledger_run_id"] == ledger_run and current["source"] == "record"
    assert current["record_run_id"] == platform_run_id and current["customer_id"] == "CU9001"
    with session(db) as s:
        assert s.get(Run, ledger_run).record_run_id == live_id
        recorded = s.scalars(select(EventRaw).where(EventRaw.run_id == live_id)).one()
        replayed = s.scalars(select(EventRaw).where(EventRaw.run_id == ledger_run)).one()
    was, now = recorded.event["authorization"], replayed.event["authorization"]
    assert now["authorization_id"] != was["authorization_id"]
    assert now["source_authorization_id"] == was["source_authorization_id"]
    keep = ("items", "billing_amount_chf", "timestamp", "merchant")
    assert {k: now[k] for k in keep} == {k: was[k] for k in keep}
    rows = [d for d in await run.decisions("CU9001") if d["run_id"] == ledger_run]
    assert [d["authorization_id"] for d in rows] == [now["authorization_id"]]
    assert rows[0]["reason_codes"] and rows[0]["message"]

    assert _posts(db) == posts  # no run, no decision and no /resolve sent anywhere
    assert [(len(a.decisions), len(a.resolutions)) for a in fake.all_auths()] == platform
    assert len(fake.runs) == 1


def test_without_the_platform_only_the_local_pack_is_known(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url) as run:
            customers = {c["customer_id"] for c in (await run.get("/api/customers")).json()["customers"]}
            assert "CU9001" not in customers and "CU0001" in customers
            r = await run.post("/api/cards/CA9001/policy-drafts", json={"instruction": "Groceries only."})
            assert r.status_code == 404, r.text

    asyncio.run(scenario())
