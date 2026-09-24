"""One polling worker per store: the worker lease (docs/database.md §5.1).

The sandbox serves each request to whichever process polls first, so two workers on one
team key race each other (docs/decisions.md, laptop run diagnosis). On Postgres the worker
holds a session-level advisory lock on a dedicated connection, outside the pool, for as long
as it polls; a second process that cannot take it stays in standby. Closing the
connection (stop, crash, lost network) releases the lock server-side.

SQLite is a single-process store (tests and local runs): the lease is always granted.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from oneguard.store.db import make_engine

log = logging.getLogger(__name__)

WORKER_LOCK_KEY = 0x0E6A_2D5F_1C3B_7A91
"""The advisory lock key of the Viseca worker (a fixed signed 64-bit number)."""


class WorkerLease(Protocol):
    def acquire(self) -> bool:
        """True once this process holds the lease (again: True while it still does)."""

    def held(self) -> bool:
        """True while the lease is still this process's."""

    def release(self) -> None:
        """Give the lease up; idempotent."""


class AlwaysLease:
    """The lease on a store only one process uses (SQLite)."""

    def acquire(self) -> bool:
        return True

    def held(self) -> bool:
        return True

    def release(self) -> None:
        return None


class AdvisoryLease:
    """``pg_try_advisory_lock`` held on one dedicated autocommit connection.

    Calls are blocking; the worker makes them from a thread. ``held`` is a round trip on
    that connection: if it fails the connection (and with it the lock) is gone.
    """

    def __init__(self, db: Engine, key: int = WORKER_LOCK_KEY) -> None:
        self._engine = make_engine(db.url, pooled=False)
        self._key = key
        self._conn: Connection | None = None
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._conn is not None:
                if self._alive():
                    return True
                self._close()
            conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            try:
                got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._key}).scalar())
            except BaseException:
                conn.close()
                raise
            if not got:
                conn.close()
                return False
            self._conn = conn
            return True

    def held(self) -> bool:
        with self._lock:
            if self._conn is None:
                return False
            if self._alive():
                return True
            self._close()
            return False

    def release(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._key})
                except (SQLAlchemyError, OSError):
                    log.warning("worker lease unlock failed; closing its connection releases it")
                self._close()
            self._engine.dispose()

    def _alive(self) -> bool:
        assert self._conn is not None
        try:
            self._conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            log.warning("worker lease connection lost: %s", type(exc).__name__)
            return False
        return True

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except (SQLAlchemyError, OSError) as exc:
                log.warning("closing the worker lease connection failed: %s", type(exc).__name__)
            self._conn = None


def worker_lease(db: Engine) -> WorkerLease:
    """The lease for ``db``'s backend: an advisory lock on Postgres, always granted on SQLite."""
    if db.url.get_backend_name() == "postgresql":
        return AdvisoryLease(db)
    return AlwaysLease()
