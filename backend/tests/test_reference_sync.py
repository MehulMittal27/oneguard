"""Judge-driven changes the worker absorbs at runtime (docs/decisions.md, hardening).

- the served reference data is synced again while the worker polls: a new bootstrap
  ``pack_version``, other ``/v1/reference-data`` row counts, an event naming ids the store
  does not know; only in the lease holder, concurrent triggers coalesce, a failure never
  blocks a decision;
- an event with properties the schema does not list is decided, one missing a required
  field is declined naming it;
- every run start re-reads ``/v1/bootstrap`` (human window, decision deadline, long poll).
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine

from oneguard.replay.events import Pack, build_events
from oneguard.store.db import session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import Card, Item, Merchant, ScenarioCatalogue
from oneguard.viseca import worker as worker_module
from oneguard.viseca.schema import check_event
from oneguard.viseca.worker import VisecaWorker
from tests.fake_viseca import JUDGING_EXTRA, FakeViseca
from tests.test_worker import (  # noqa: F401  fixtures
    LEASE_OPTIONS,
    SharedLease,
    db,
    fake_client,
    fast,
    harness,
    history,
    ledger_kind,
    seeded_db,
    start_run,
    wait_until,
)

REAL_ENGINE = {"implementations": None, "stubbed": None}


def edit(**changes: Any) -> Callable[[dict[str, Any]], None]:
    """An event rewrite: ``merchant``, ``item``, ``customer``, ``card`` ids and ``extras``
    (dotted path → value) for properties the schema does not list."""

    def apply(data: dict[str, Any]) -> None:
        auth, mandate = data["authorization"], data["mandate"]
        if "merchant" in changes:
            auth["merchant"]["merchant_id"] = changes["merchant"]
            auth["merchant"]["merchant_name"] = "Brand New Grocer"
        if "item" in changes:
            auth["items"][0]["item_id"] = changes["item"]
        if "customer" in changes:
            mandate["customer_id"] = changes["customer"]
        if "card" in changes:
            auth["card_id"] = mandate["card_id"] = changes["card"]
        for path, value in changes.get("extras", {}).items():
            *parents, name = path.split(".")
            node = data
            for parent in parents:
                node = node[parent]
            node[name] = value

    return apply


def reference_rows(decision: dict[str, Any]) -> list[str]:
    return [row["detail"] for row in decision["evidence"] if row["rule"] == "reference_data"]


def unknown_row(names: str) -> str:
    return (
        f"Not in the reference data: {names}. Their catalogue prices and purchase history "
        "count as unknown, never as familiar."
    )


def served_only(*tables: str) -> dict[str, list[dict[str, Any]]]:
    return {t: copy.deepcopy(JUDGING_EXTRA[t]) for t in tables}


# Tolerant event validation ----------------------------------------------------------------


def test_extra_properties_are_named_not_errors_and_a_missing_field_is_named() -> None:
    data = build_events(Pack.load(), "SCEN0000", mandate_id="TM1")[0]
    assert check_event(data).errors == [] and check_event(data).extras == []

    edit(extras={"authorization.loyalty_points": 12, "mandate.agent_platform": "shopbot", "top_level_note": "x"})(data)
    checked = check_event(data)
    assert checked.errors == []
    assert checked.extras == ["authorization.loyalty_points", "mandate.agent_platform", "top_level_note"]

    del data["authorization"]["merchant"]
    data["authorization"]["amount"] = "20"
    checked = check_event(data)
    assert checked.errors == ["authorization.merchant is missing", "authorization.amount: '20' is not of type 'number'"]


# Unknown ids --------------------------------------------------------------------------------


def test_an_event_with_an_unknown_merchant_item_and_extra_fields_is_decided_normally(
    db: Engine, history: StoreHistoryIndex, caplog: pytest.LogCaptureFixture  # noqa: F811
) -> None:
    """No crash, no auto-decline: the unknown ids read as unknown (no price range, no
    history), the extras are logged and passed on, and a sync that did not bring the ids
    is not repeated for the next event naming them."""
    strange = edit(
        merchant="ME7777",
        item="IT7777",
        extras={"authorization.loyalty_points": 12, "mandate.agent_platform": "shopbot"},
    )

    async def scenario() -> None:
        config = fast(rewrite={"AU0001": strange})
        async with harness(db, config, history=history, **REAL_ENGINE) as (fake, client, worker):
            await worker.start()
            assert worker.reference_syncs == 1
            for _ in range(2):
                _, run_id = await start_run(client, worker, "SCEN0000")
                (auth,) = fake.runs[run_id].auths
                await wait_until(lambda: bool(auth.decisions))  # noqa: B023
                posted = auth.decisions[0]
                assert not auth.auto_declined
                assert posted["decision"] == "approve", posted  # no rules: nothing to stop it
                assert "unevaluable" not in posted["reason_codes"]
                assert not any(row["rule"] == "event_schema" for row in posted["evidence"])
                assert reference_rows(posted) == [unknown_row("merchant ME7777, item IT7777")]
            # one sync for the unknown ids; the second event named ids that sync did not bring
            assert worker.reference_syncs == 2
            (entry,) = [e for e in await worker.ledger_entries(list(worker.source_ids)) if e.outcome == "approve"][:1]
            assert entry.merchant_id == "ME7777"

    caplog.set_level("INFO", logger="oneguard.viseca.worker")
    asyncio.run(scenario())
    assert (
        "carries properties the event schema does not list, passed on unread: "
        "authorization.loyalty_points, mandate.agent_platform" in caplog.text
    )
    assert "the reference data does not have merchant ME7777, item IT7777 either" in caplog.text


def test_an_unknown_merchant_is_never_a_known_shop(db: Engine, history: StoreHistoryIndex) -> None:  # noqa: F811
    rule = {"field": "merchant.familiar_on_card", "operator": "=", "value": "true"}

    async def scenario() -> None:
        config = fast(rewrite={"AU0001": edit(merchant="ME7777")})
        async with harness(db, config, history=history, **REAL_ENGINE) as (fake, client, worker):
            await worker.start()
            _, run_id = await start_run(client, worker, "SCEN0000", hard_rules=[rule])
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions))
            posted = auth.decisions[0]
            assert posted["decision"] in ("decline", "step_up"), posted
            assert reference_rows(posted)

    asyncio.run(scenario())


def test_an_unknown_card_and_customer_decide_while_the_reference_data_is_down(
    db: Engine, history: StoreHistoryIndex  # noqa: F811
) -> None:
    """A sync that fails is logged and never blocks the decision."""

    async def scenario() -> None:
        config = fast(rewrite={"AU0001": edit(customer="CU7777", card="CA7777")})
        async with harness(db, config, history=history, **REAL_ENGINE) as (fake, client, worker):
            await worker.start()
            fake.config.reference_status = 503
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions))
            posted = auth.decisions[0]
            assert posted["decision"] == "approve" and not auth.auto_declined, posted
            assert reference_rows(posted) == [unknown_row("customer CU7777, card CA7777")]
            assert "reference data unavailable" in (worker.status().last_error or "")
            assert worker.status().state == "polling"

    asyncio.run(scenario())


def test_an_unknown_id_the_platform_serves_is_synced_before_the_decision(
    db: Engine, history: StoreHistoryIndex  # noqa: F811
) -> None:
    async def scenario() -> None:
        config = fast(decision_deadline_s=8.0, rewrite={"AU0001": edit(merchant="ME9001", item="IT9001")})
        options = {"history": history, "unknown_id_sync_s": 5.0, **REAL_ENGINE}  # the bound has its own test
        async with harness(db, config, **options) as (fake, client, worker):
            await worker.start()
            fake.config.served_extra = served_only("merchants", "items")  # a judge adds them now
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions))
            assert reference_rows(auth.decisions[0]) == []  # known by the time it was decided
            assert worker.reference_syncs == 2
            assert worker.history.merchant_names(["ME9001"]) == {"ME9001": "Served Corner Shop"}
            assert worker.history.item_price_range("IT9001") == (2.0, 4.5, 9.0)
            with session(db) as s:
                assert s.get(Merchant, "ME9001") is not None and s.get(Item, "IT9001") is not None

    asyncio.run(scenario())


def test_the_unknown_id_sync_is_bounded_and_finishes_in_the_background(
    db: Engine, history: StoreHistoryIndex  # noqa: F811
) -> None:
    async def scenario() -> None:
        config = fast(rewrite={"AU0001": edit(merchant="ME9001")})
        options = {"history": history, "unknown_id_sync_s": 0.3, **REAL_ENGINE}
        async with harness(db, config, **options) as (fake, client, worker):
            await worker.start()
            fake.config.served_extra = served_only("merchants")
            fake.config.reference_delay_s = 4.0
            _, run_id = await start_run(client, worker, "SCEN0000")
            (auth,) = fake.runs[run_id].auths
            await wait_until(lambda: bool(auth.decisions))
            assert auth.accepted_at is not None and auth.queued_at is not None
            assert (auth.accepted_at - auth.queued_at).total_seconds() < 2.5  # did not wait the 4 s
            assert reference_rows(auth.decisions[0])  # decided with what the store had
            await wait_until(lambda: worker.reference_syncs == 2)
            assert worker.history.merchant_names(["ME9001"]) == {"ME9001": "Served Corner Shop"}

    asyncio.run(scenario())


# Reference-data changes -----------------------------------------------------------------------


def test_other_reference_data_counts_trigger_a_sync(db: Engine, history: StoreHistoryIndex) -> None:  # noqa: F811
    async def scenario() -> None:
        async with harness(db, fast(), history=history, reference_check_s=0.1) as (fake, _, worker):
            await worker.start()
            reads = fake.reference_reads
            await wait_until(lambda: fake.reference_reads >= reads + 3)
            assert worker.reference_syncs == 1  # checked, unchanged: not synced

            fake.config.served_extra = served_only("customers", "accounts", "cards")
            await wait_until(lambda: worker.reference_syncs == 2)
            with session(db) as s:
                assert s.get(Card, "CA9001") is not None
            await asyncio.sleep(0.4)
            assert worker.reference_syncs == 2  # the new counts are the ones compared from now on

    asyncio.run(scenario())


def test_a_new_pack_version_at_a_run_start_syncs_first(
    db: Engine, history: StoreHistoryIndex, caplog: pytest.LogCaptureFixture  # noqa: F811
) -> None:
    async def scenario() -> None:
        async with harness(db, fast(), history=history) as (fake, _, worker):
            await worker.start()
            await worker.refresh_bootstrap("run start")
            assert worker.reference_syncs == 1  # same pack: bootstrap read, nothing synced

            fake.config.pack_version = "saw27"
            fake.config.served_extra = served_only("scenario_catalogue")
            await worker.refresh_bootstrap("run start")
            assert worker.pack_version == "saw27" and worker.reference_syncs == 2
            with session(db) as s:
                assert s.get(ScenarioCatalogue, "SCEN9001") is not None
            assert "SCEN9001" in (worker.served_scenarios or [])

    caplog.set_level("INFO", logger="oneguard.viseca.worker")
    asyncio.run(scenario())
    assert "Viseca bootstrap changed (run start): pack_version saw26 -> saw27" in caplog.text


def test_concurrent_sync_triggers_coalesce(db: Engine, history: StoreHistoryIndex) -> None:  # noqa: F811
    async def scenario() -> None:
        async with harness(db, fast(), history=history) as (fake, _, worker):
            await worker.start()
            reads = fake.reference_reads
            fake.config.reference_delay_s = 0.3
            await asyncio.gather(*(worker._sync_reference(f"trigger {i}") for i in range(5)))
            assert fake.reference_reads == reads + 1
            assert worker.reference_syncs == 2

    asyncio.run(scenario())


def test_only_the_lease_holder_syncs(db: Engine, history: StoreHistoryIndex, ledger_kind: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast())
        lease = SharedLease()
        first_client, second_client = fake_client(fake, db), fake_client(fake, db)
        first = VisecaWorker(first_client, db=db, history=history, lease=lease.of("first"), **LEASE_OPTIONS)
        second = VisecaWorker(second_client, db=db, history=history, lease=lease.of("second"), **LEASE_OPTIONS)
        try:
            await first.start()
            await second.start()
            assert second.status().state == "standby"
            assert (first.reference_syncs, second.reference_syncs) == (1, 0)
            assert await second._sync_reference("a test") is False and second.reference_syncs == 0
            await second.refresh_bootstrap("run start")
            assert second.reference_syncs == 0

            await first.stop()
            await wait_until(lambda: second.status().state == "polling")
            assert second.reference_syncs == 1  # the new holder syncs when it takes over
        finally:
            await first.stop()
            await second.stop()
            await first_client.aclose()
            await second_client.aclose()

    asyncio.run(scenario())


# Bootstrap re-read at run start -----------------------------------------------------------------


def test_every_run_start_rereads_the_bootstrap_and_uses_changed_values(
    db: Engine, history: StoreHistoryIndex, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(worker_module, "BOOTSTRAP_FRESH_S", 0.0)

    async def scenario() -> None:
        async with harness(db, fast(human_window_s=60.0), history=history) as (fake, client, worker):
            await worker.start()
            assert (worker.human_window_s, worker.decision_deadline_s, worker.poll_wait_s) == (60.0, 3.0, 0.2)
            _, first_run = await start_run(client, worker, "SCEN0000")
            await wait_until(lambda: fake.bootstrap_reads == 2)  # the run start re-read it
            await wait_until(lambda: worker._runs[first_run].ctx is not None)

            fake.config.human_window_s = 30.0
            fake.config.decision_deadline_s = 4.0
            fake.config.long_poll_max_s = 0.1
            await start_run(client, worker, "SCEN0000")
            await wait_until(lambda: worker.human_window_s == 30.0)
            assert (worker.decision_deadline_s, worker.poll_wait_s) == (4.0, 0.1)
            assert worker._runs[first_run].ctx.human_window_s == 30  # used from now on, in every run

            fake.config.long_poll_max_s = 25.0
            await worker.refresh_bootstrap("run start")
            assert worker.poll_wait_s == 0.2  # the configured wait again, never above it

    caplog.set_level("INFO", logger="oneguard.viseca.worker")
    asyncio.run(scenario())
    assert (
        "Viseca bootstrap changed (run run_" in caplog.text
        and "human window (s) 60.0 -> 30.0; decision deadline (s) 3.0 -> 4.0; long-poll wait (s) 0.2 -> 0.1"
        in caplog.text
    )
