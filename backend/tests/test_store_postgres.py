"""test_store.py's database assertions against a real Postgres (the Supabase session pooler).

Skipped unless ``ONEGUARD_TEST_DATABASE_URL`` is set; CI never sets it. The database may
be shared, so each run works in a schema of its own (``oneguard_test_<hex>``): every
connection's ``search_path`` points at it, and teardown drops only that schema. The
``public`` tables that ``make seed`` owns are never read or written here.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from oneguard.store import seed as seed_module
from oneguard.store.db import (
    POSTGRES_POOL_SIZE,
    POSTGRES_STATEMENT_TIMEOUT_MS,
    make_engine,
    normalise_url,
    session,
)
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.lease import AdvisoryLease, worker_lease
from tests.test_store import (  # noqa: F401  collected again here, against the fixtures below
    test_a_bad_served_table_leaves_the_store_as_it_was,
    test_every_reference_table_matches_the_pack,
    test_every_table_in_database_md_exists,
    test_familiarity_counts_approved_purchases_only,
    test_history_index_for_cu0019,
    test_history_index_other_answers,
    test_naive_datetimes_are_refused,
    test_recent_rows_window,
    test_runtime_tables_round_trip_json_and_utc,
    test_seed_is_idempotent,
    test_seed_loads_every_reference_table,
    test_served_superset_is_upserted_once_and_nothing_is_deleted,
    test_served_tables_matching_the_store_change_nothing,
    test_values_come_back_typed_and_in_utc,
)

TEST_DATABASE_URL_ENV = "ONEGUARD_TEST_DATABASE_URL"
URL = normalise_url(os.environ.get(TEST_DATABASE_URL_ENV, "").strip())
TIMEOUT_SHOWN = f"{POSTGRES_STATEMENT_TIMEOUT_MS // 1000}s"  # how SHOW prints 5000 ms

if not URL:
    pytest.skip(f"{TEST_DATABASE_URL_ENV} not set", allow_module_level=True)


def _use_schema(schema: str) -> Any:
    def listener(dbapi_connection: Any, _record: Any) -> None:
        autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            dbapi_connection.execute(f'SET search_path TO "{schema}"')
        finally:
            dbapi_connection.autocommit = autocommit

    return listener


@pytest.fixture(scope="module")
def schema() -> str:
    return f"oneguard_test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine(schema: str) -> Iterator[Engine]:
    admin = make_engine(URL)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(URL)
    event.listen(engine, "connect", _use_schema(schema), insert=True)
    try:
        seed_module.run(engine=engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(scope="module")
def history(engine: Engine) -> StoreHistoryIndex:
    with session(engine) as s:
        return StoreHistoryIndex.load(s)


def test_connections_use_the_isolated_schema_tls_and_timeout(engine: Engine, schema: str) -> None:
    with engine.connect() as c:
        assert c.execute(text("select current_schema()")).scalar() == schema
        assert c.execute(text("show statement_timeout")).scalar() == TIMEOUT_SHOWN
        assert c.connection.dbapi_connection.pgconn.ssl_in_use
    assert engine.pool.size() == POSTGRES_POOL_SIZE and engine.pool._max_overflow == 0


def test_statement_timeout_survives_a_pool_reset(engine: Engine) -> None:
    for _ in range(3):  # each checkout returns the connection with a rollback
        with engine.connect() as c:
            c.execute(text("select 1"))
    with engine.connect() as c:
        assert c.execute(text("show statement_timeout")).scalar() == TIMEOUT_SHOWN


def test_statement_timeout_cancels_a_slow_query(engine: Engine) -> None:
    with pytest.raises(OperationalError, match="statement timeout"), engine.connect() as c:
        c.execute(text("select pg_sleep(:s)"), {"s": POSTGRES_STATEMENT_TIMEOUT_MS / 1000 + 1})


def test_prepared_statements_work_through_the_pooler(engine: Engine) -> None:
    # psycopg prepares a query server-side from its 5th execution on one connection.
    for _ in range(3):
        with engine.connect() as c:
            for i in range(8):
                assert c.execute(text("select count(*) + :i from customers"), {"i": i}).scalar() == 20 + i
            assert c.execute(text("select count(*) from pg_prepared_statements")).scalar() >= 1
        engine.dispose()  # the next round gets a new pooler client on a reused server connection


def test_password_never_reaches_logs_or_reprs(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    password = make_url(URL).password
    assert password
    caplog.set_level(logging.DEBUG, logger="sqlalchemy")  # sqlalchemy defaults itself to WARN
    engine.dispose()
    with session(engine) as s:
        s.execute(text("select count(*) from merchants")).scalar()
    exposed = {
        "repr(engine)": repr(engine),
        "str(engine.url)": str(engine.url),
        "repr(engine.url)": repr(engine.url),
        "logs": caplog.text,
    }
    assert "select count(*) from merchants" in caplog.text  # the engine did log
    assert [where for where, logged in exposed.items() if password in logged] == []


# The worker lease (store/lease.py) -------------------------------------------------------


def _lease_key() -> int:
    """A key of this test run's own: advisory locks are database-wide, and the live worker
    may hold ``WORKER_LOCK_KEY`` on a shared database."""
    return uuid.uuid4().int >> 65


def _lease_holder_pid(engine: Engine, key: int) -> int | None:
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted"
                " AND classid = :hi AND objid = :lo AND objsubid = 1"
            ),
            {"hi": key >> 32, "lo": key & 0xFFFFFFFF},
        ).scalar()


def test_only_one_worker_lease_is_granted_and_it_is_handed_on(engine: Engine) -> None:
    assert isinstance(worker_lease(engine), AdvisoryLease)
    key = _lease_key()
    first, second = AdvisoryLease(engine, key), AdvisoryLease(engine, key)
    try:
        assert first.acquire() and first.acquire()  # held: asking again keeps it
        assert not second.acquire() and not second.held()
        assert first.held() and _lease_holder_pid(engine, key) is not None
        assert engine.pool.checkedout() == 0  # its connection is not one of the pool's
        first.release()
        assert not first.held() and _lease_holder_pid(engine, key) is None
        assert second.acquire() and second.held()
    finally:
        first.release()
        second.release()


def test_a_worker_lease_whose_connection_dies_is_lost_and_can_be_taken_again(engine: Engine) -> None:
    key = _lease_key()
    lease, other = AdvisoryLease(engine, key), AdvisoryLease(engine, key)
    try:
        assert lease.acquire()
        pid = _lease_holder_pid(engine, key)
        with engine.connect() as c:
            assert c.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar()
        assert not lease.held()  # the lock went with the connection
        assert other.acquire()  # so another process may take it
        assert not lease.acquire()
        other.release()
        assert lease.acquire() and lease.held()
    finally:
        lease.release()
        other.release()
