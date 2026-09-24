"""Database engine and sessions (docs/database.md §1, §4, §5).

``ONEGUARD_DATABASE_URL`` selects the database: unset → ``sqlite:///./oneguard.sqlite``;
Supabase → the session-mode pooler URL (port 5432). ``postgres://`` and
``postgresql://`` URLs are pinned to the psycopg (v3) driver.

Postgres connections (docs/database.md §5): at most ``POSTGRES_POOL_SIZE`` per process
(no overflow), pre-pinged, TLS required unless the URL names an ``sslmode``, and
``statement_timeout`` set with ``SET`` on every new connection, because the Supabase
pooler (Supavisor) ignores the libpq ``options`` startup parameter.

The URL carries the database password: never log or print it; render it with
``url.render_as_string(hide_password=True)`` if it must appear anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from typing import Any

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

DATABASE_URL_ENV = "ONEGUARD_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./oneguard.sqlite"
POSTGRES_POOL_SIZE = 5
POSTGRES_STATEMENT_TIMEOUT_MS = 5000


def normalise_url(url: str) -> str:
    """``url`` with ``postgres://`` and ``postgresql://`` pinned to psycopg (v3)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def database_url() -> str:
    """The configured URL, normalised to an explicit driver."""
    return normalise_url(os.environ.get(DATABASE_URL_ENV, "").strip() or DEFAULT_DATABASE_URL)


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    # WAL: readers never wait for the writer and a commit is one append, so the worker's
    # concurrent writes (call log, events_raw, run rows, cursor, ledger) do not stall its
    # loop past a decision deadline, as the rollback journal did (docs/database.md §1).
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def _set_postgres_statement_timeout(dbapi_connection: Any, _record: Any) -> None:
    # Session-level and outside any transaction, so a pool reset (rollback) keeps it.
    autocommit = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    try:
        dbapi_connection.execute(f"SET statement_timeout = {POSTGRES_STATEMENT_TIMEOUT_MS}")
    finally:
        dbapi_connection.autocommit = autocommit


def make_engine(url: str | URL | None = None, *, pooled: bool = True) -> Engine:
    """A new engine for ``url`` (default: ``database_url()``).

    ``pooled=False`` (Postgres only): every connection is opened for its caller and closed
    with it, outside the process's pool of ``POSTGRES_POOL_SIZE`` (the worker lease).
    """
    parsed = url if isinstance(url, URL) else make_url(normalise_url(url or database_url()))
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        engine = create_engine(
            parsed, pool_pre_ping=True, connect_args={"check_same_thread": False}
        )
        event.listen(engine, "connect", _configure_sqlite)
        return engine
    if backend == "postgresql":
        pool: dict[str, Any] = (
            {"pool_pre_ping": True, "pool_size": POSTGRES_POOL_SIZE, "max_overflow": 0}
            if pooled
            else {"poolclass": NullPool}
        )
        engine = create_engine(
            parsed,
            **pool,
            connect_args={} if "sslmode" in parsed.query else {"sslmode": "require"},
        )
        event.listen(engine, "connect", _set_postgres_statement_timeout)
        return engine
    raise ValueError(f"unsupported database {backend!r}; use sqlite or postgresql+psycopg")


@cache
def get_engine(url: str | None = None) -> Engine:
    """The process-wide engine for ``url`` (default: ``database_url()``)."""
    return make_engine(url)


@cache
def _sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session(engine: Engine | None = None) -> Iterator[Session]:
    """One transaction: commits on success, rolls back on any exception."""
    with _sessionmaker(engine or get_engine())() as s:
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise


def init_db(engine: Engine | None = None) -> None:
    """Create every table that does not exist yet (no migrations, docs/database.md §4)."""
    from oneguard.store.schema import Base

    Base.metadata.create_all(engine or get_engine())


def drop_db(engine: Engine | None = None) -> None:
    """Drop every table. Callers guard against production (``make reset-db``)."""
    from oneguard.store.schema import Base

    Base.metadata.drop_all(engine or get_engine())
