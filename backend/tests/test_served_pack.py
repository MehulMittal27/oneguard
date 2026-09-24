"""A sandbox serving a superset of ``data/`` (the judging pack): the app accepts its ids.

The worker syncs the served reference tables at start; then C12 lists the served
customer with the bootstrap profile's card and scenario, C10 and C1 work on the
served-only card, D3 starts a run of a served-only scenario and D2 still replays only
what the local pack has purchases for.
"""

from __future__ import annotations

import asyncio

from tests.fake_viseca import FakeViseca, judging_pack
from tests.test_api_contract import (  # noqa: F401  fixtures
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

            started = await run.post("/api/dev/runs", json={"scenario_id": "SCEN9001", "card_id": "CA9001"})
            assert started.status_code == 200, started.text
            assert started.json()["scenario_id"] == "SCEN9001"
            assert [r.scenario_id for r in fake.runs.values()] == ["SCEN9001"]
            await until(lambda: run.services.worker.live_run(started.json()["run_id"]).decided == 1)

            replay = await run.post(
                "/api/dev/replay/restart", json={"scenario_id": "SCEN9001", "card_id": "CA9001", "speed_ms": 0}
            )
            assert replay.status_code == 404, replay.text

    asyncio.run(scenario())


def test_without_the_platform_only_the_local_pack_is_known(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url) as run:
            customers = {c["customer_id"] for c in (await run.get("/api/customers")).json()["customers"]}
            assert "CU9001" not in customers and "CU0001" in customers
            r = await run.post("/api/cards/CA9001/policy-drafts", json={"instruction": "Groceries only."})
            assert r.status_code == 404, r.text

    asyncio.run(scenario())
