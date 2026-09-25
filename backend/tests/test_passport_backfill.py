"""The first start after the passport deploy, on a store the old code wrote (docs/passport.md).

The store is built as it is on Supabase today: reference data, mandates (one active, one
revoked) and a replayed run's decisions, without the passport tables and without the
three new columns. Then the new code starts on it: ``init_db`` adds only what is missing,
and one book sync issues a passport for every active mandate and a receipt for every
decision. Nothing is deleted or rewritten, everything verifies, and a second start changes
nothing. Runs on SQLite, and on Postgres when ``ONEGUARD_TEST_DATABASE_URL`` is set (a
throwaway schema, as ``test_store_postgres.py``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, inspect, select, text

from oneguard.api import policies
from oneguard.passport import devices as device_store
from oneguard.passport.book import PassportBook
from oneguard.passport.signer import DeviceKey
from oneguard.replay.events import Pack
from oneguard.replay.matrix import POLICIES
from oneguard.replay.runner import decide_all, load_policy
from oneguard.store import seed as seed_module
from oneguard.store.db import init_db, make_engine, normalise_url, session
from oneguard.store.schema import Decision, EventRaw, Mandate, Passport, Receipt

NEW_TABLES = ("receipts", "passports", "device_nonces", "devices", "signing_keys")
NEW_COLUMNS = (("decisions", "would_approve_if"), ("decisions", "receipt_id"), ("mandates", "passport_id"))
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
PG_URL = normalise_url(os.environ.get("ONEGUARD_TEST_DATABASE_URL", "").strip())


def _sqlite(tmp_path: Path) -> Iterator[Engine]:
    engine = make_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    yield engine
    engine.dispose()


def _postgres() -> Iterator[Engine]:
    schema = f"oneguard_test_{uuid.uuid4().hex[:12]}"
    admin = make_engine(PG_URL)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))

    def use_schema(dbapi_connection: Any, _record: Any) -> None:
        autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            dbapi_connection.execute(f'SET search_path TO "{schema}"')
        finally:
            dbapi_connection.autocommit = autocommit

    engine = make_engine(PG_URL)
    event.listen(engine, "connect", use_schema, insert=True)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def old_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    """A seeded store with a replayed run and two mandates, in the pre-passport schema."""
    if request.param == "postgres" and not PG_URL:
        pytest.skip("ONEGUARD_TEST_DATABASE_URL not set")
    stores = _sqlite(tmp_path) if request.param == "sqlite" else _postgres()
    engine = next(stores)
    seed_module.run(engine=engine)
    policy = load_policy(POLICIES / "SCEN0001.yaml", mandate_id="md_old_active")
    decide_all(Pack.load(), "SCEN0001", policy, False, db=engine)
    with session(engine) as s:
        for mandate_id, status in (("md_old_active", "active"), ("md_old_revoked", "revoked")):
            s.add(Mandate(
                mandate_id=mandate_id, viseca_mandate_id=None, card_id="CA0001", customer_id="CU0001",
                instruction=policy.instruction, rules=policies.store_rules(policy.rules, policies.flags_of(policy)),
                checks=[policies.rule_check(r).model_dump(mode="json") for r in policy.rules],
                uncertainty_policy=policy.uncertainty_policy, open_questions=[], status=status,
                confirmed_at=NOW, revoked_at=NOW if status == "revoked" else None,
            ))  # fmt: skip
    with engine.begin() as c:  # back to the schema the old code created
        for table in NEW_TABLES:
            c.execute(text(f"DROP TABLE {table}"))
        for table, column in NEW_COLUMNS:
            c.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    yield engine
    next(stores, None)


def _rows(engine: Engine) -> dict[str, int]:
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in ("decisions", "mandates", "events_raw")}


def test_the_first_start_adds_only_what_is_missing_and_backfills_every_document(old_store: Engine) -> None:
    before = _rows(old_store)
    columns = {t: {c["name"] for c in inspect(old_store).get_columns(t)} for t in ("decisions", "mandates")}
    assert "receipt_id" not in columns["decisions"] and "passport_id" not in columns["mandates"]

    added = init_db(old_store)
    assert sorted(added) == sorted(f"{t}.{c}" for t, c in NEW_COLUMNS)
    assert init_db(old_store) == []  # idempotent

    book = PassportBook.open(old_store, lambda: NOW)
    counts = book.sync()
    assert counts.passports == 1  # the active mandate; a mandate revoked before passports gets none
    assert counts.receipts == before["decisions"] == 10
    assert _rows(old_store) == before  # nothing deleted, nothing added to the old tables

    with session(old_store) as s:
        passports = list(s.scalars(select(Passport)))
        receipts = list(s.scalars(select(Receipt)))
        decisions = list(s.scalars(select(Decision)))
        mandates = {m.mandate_id: m for m in s.scalars(select(Mandate))}
    (passport,) = passports
    assert (passport.mandate_id, passport.version, passport.reason) == ("md_old_active", 1, "backfill")
    assert passport.document["devices"] == [] and passport.document["revoked_at"] is None
    assert mandates["md_old_active"].passport_id == passport.passport_id and mandates["md_old_revoked"].passport_id is None
    assert book.verify(passport.document, passport.signature, passport.key_id)[0]

    assert {r.live_authorization_id for r in receipts} == {d.live_authorization_id for d in decisions}
    by_live = {r.live_authorization_id: r for r in receipts}
    for decision in decisions:
        receipt = by_live[decision.live_authorization_id]
        assert decision.receipt_id == receipt.receipt_id  # filled in for the old rows
        assert decision.would_approve_if is None  # never invented after the fact
        assert receipt.document["passport_id"] == passport.passport_id and receipt.document["passport_version"] == 1
        assert receipt.document["permitted"], decision.live_authorization_id
        assert book.verify(receipt.document, receipt.signature, receipt.key_id)[0]

    assert book.sync() == type(counts)()  # a second start issues nothing

    # the backfilled passport has no device: the card's first device enrols without approval
    device, _ = device_store.enrol(old_store, "CA0001", "CU0001", DeviceKey().jwk, "Stage laptop", NOW)
    assert device.status == "enrolled"
    assert book.sync_passports(card_id="CA0001") == 1
    with session(old_store) as s:
        latest = s.scalar(select(Passport).where(Passport.version == 2))
        assert s.scalar(select(func.count()).select_from(EventRaw)) == before["events_raw"]
    assert latest is not None and latest.reason == "devices" and len(latest.document["devices"]) == 1
