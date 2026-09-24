"""Database engine and sessions (docs/database.md §1, §4, §5).

``ONEGUARD_DATABASE_URL`` selects the database: unset → ``sqlite:///./oneguard.sqlite``;
Supabase → the session-mode pooler URL (port 5432). ``postgres://`` and
``postgresql://`` URLs are pinned to the psycopg (v3) driver.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL_ENV = "ONEGUARD_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./oneguard.sqlite"
POSTGRES_POOL_SIZE = 5
POSTGRES_STATEMENT_TIMEOUT_MS = 5000


def database_url() -> str:
    """The configured URL, normalised to an explicit driver."""
    url = os.environ.get(DATABASE_URL_ENV, "").strip() or DEFAULT_DATABASE_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    """A new engine for ``url`` (default: ``database_url()``)."""
    url = url or database_url()
    backend = make_url(url).get_backend_name()
    if backend == "sqlite":
        engine = create_engine(
            url, pool_pre_ping=True, connect_args={"check_same_thread": False}
        )
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    if backend == "postgresql":
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=POSTGRES_POOL_SIZE,
            connect_args={"options": f"-c statement_timeout={POSTGRES_STATEMENT_TIMEOUT_MS}"},
        )
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
