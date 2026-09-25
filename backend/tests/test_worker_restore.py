"""A restarted worker decides from our confirmed policy, not the platform snapshot.

``bind_policy`` is in memory. Without a restore, every live run after a restart or a
redeploy was decided from the mandate snapshot's ``hard_rules`` alone, which drops the
requested item (C5), "nothing extra" (C10) and ``on_fail: ask``. The worker now loads
the confirmed mandate stored for that Viseca id first; the snapshot is used only when
there is none.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from oneguard.api import policies
from oneguard.engine.types import Rule
from oneguard.replay.events import all_events
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.schema import Mandate
from tests.test_worker import fast, harness, start_run, wait_until

SCENARIO = "SCEN0004"  # the monitor: requested item + nothing extra
EVENT = all_events()[SCENARIO][0]
CARD, CUSTOMER = EVENT["authorization"]["card_id"], EVENT["mandate"]["customer_id"]
RULES = [
    Rule(id="C1", field="authorization.billing_amount_chf", operator="<=", value=400, currency="CHF",
         scope="purchase", text="Total at or below CHF 400 per order", source="exact", kind="amount"),
    Rule(id="C9-same", field="merchant.familiar_on_card", operator="=", value="true",
         text="Only shops you have bought from before; ask me if it changed", source="inferred",
         kind="merchant", on_fail="ask"),
]
FLAGS = {"requested_item": "27-inch monitor", "allowed_item_categories": None, "blocked_item_categories": None,
         "requires_known_shop": False, "nothing_extra": True, "shop_type": None}


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("restore") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture
def db(seeded: Path, tmp_path: Path) -> Engine:
    path = tmp_path / "oneguard.sqlite"
    shutil.copy(seeded, path)
    engine = make_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()


def _store_confirmed(db: Engine, viseca_mandate_id: str, status: str = "active") -> None:
    with session(db) as s:
        s.add(Mandate(
            mandate_id="M_STORED", viseca_mandate_id=viseca_mandate_id, card_id=CARD, customer_id=CUSTOMER,
            instruction="Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or "
                        "less. Do not add anything I did not ask for.",
            rules=policies.store_rules(RULES, FLAGS),
            checks=[{"id": r.id, "text": r.text, "source": r.source, "uncertainty": None, "kind": r.kind}
                    for r in RULES],
            uncertainty_policy="ask", open_questions=[], status=status,
            confirmed_at=datetime(2026, 9, 24, 12, tzinfo=UTC), revoked_at=None,
        ))


async def _policy_used(db: Engine, store_it: bool, status: str = "active"):
    async with harness(db, fast()) as (fake, client, worker):
        viseca_mandate_id, run_id = await start_run(client, worker, SCENARIO)
        if store_it:
            _store_confirmed(db, viseca_mandate_id, status)
        await worker.start()  # a fresh worker: nothing bound in memory
        auths = fake.runs[run_id].auths
        await wait_until(lambda: bool(auths[0].decisions))
        run = next(r for r in worker._runs.values() if r.ctx is not None)
        return viseca_mandate_id, run.ctx.policy


def test_a_restarted_worker_decides_from_the_stored_confirmed_policy(db: Engine):
    _, policy = asyncio.run(_policy_used(db, store_it=True))
    assert policy.mandate_id == "M_STORED"
    assert policy.requested_item == "27-inch monitor" and policy.nothing_extra is True
    assert [r.on_fail for r in policy.rules] == ["decline", "ask"]


def test_without_a_stored_policy_the_snapshot_still_decides(db: Engine):
    viseca_mandate_id, policy = asyncio.run(_policy_used(db, store_it=False))
    assert policy.mandate_id == viseca_mandate_id


def test_a_stored_revoked_policy_binds_as_revoked(db: Engine):
    _, policy = asyncio.run(_policy_used(db, store_it=True, status="revoked"))
    assert policy.mandate_id == "M_STORED" and policy.status == "revoked"
