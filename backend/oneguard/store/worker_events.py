"""``worker_events``: what must outlive the log buffer (docs/database.md §2).

Fly keeps only the last lines of a machine's log, so a platform refusal logged once is
soon gone. Every refusal the Viseca client sees is written here (``refusal_sink``), and
the platform's word on our mandate too (``mandate_inactive``, ``mandate_reregistered``).
Each insert prunes its kind to the newest ``KEEP`` rows. ``/healthz`` shows the newest
refusal. Nothing here reads a header or the key: the client scrubs the key from every
message before a refusal reaches this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, delete, select

from oneguard.store.db import session
from oneguard.store.schema import WorkerEvent
from oneguard.viseca.client import Refusal, RefusalSink

KEEP = 200
"""Rows kept per kind; the oldest go on insert."""
MESSAGE_LIMIT = 2048
"""Longest message kept (characters): the platform's message, else its response body."""

Kind = Literal["platform_refusal", "mandate_inactive", "mandate_reregistered"]


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: Kind
    action: str | None = None
    status: int | None = None
    code: str | None = None
    message: str | None = None
    mandate_id: str | None = None
    run_id: str | None = None
    authorization_id: str | None = None


def cut(text: str | None, limit: int = MESSAGE_LIMIT) -> str | None:
    """``text`` cut to at most ``limit`` characters, marked when cut."""
    if text is None or len(text) <= limit:
        return text
    marker = f"...[{len(text) - limit} more chars]"
    return text[: limit - len(marker)] + marker


def record(db: Engine, event: Event, keep: int = KEEP) -> None:
    """Insert ``event`` and prune its kind to the newest ``keep`` rows, in one transaction."""
    with session(db) as s:
        s.add(
            WorkerEvent(
                at=event.at,
                kind=event.kind,
                action=event.action,
                status=event.status,
                code=event.code,
                message=cut(event.message),
                mandate_id=event.mandate_id,
                run_id=event.run_id,
                authorization_id=event.authorization_id,
            )
        )
        s.flush()
        newest = select(WorkerEvent.id).where(WorkerEvent.kind == event.kind).order_by(WorkerEvent.id.desc()).limit(keep)
        s.execute(
            delete(WorkerEvent).where(WorkerEvent.kind == event.kind, WorkerEvent.id.not_in(newest.scalar_subquery()))
        )


def latest(db: Engine, kind: Kind, limit: int = 1) -> list[WorkerEvent]:
    """The newest ``limit`` events of ``kind``, newest first."""
    with session(db) as s:
        return list(
            s.scalars(select(WorkerEvent).where(WorkerEvent.kind == kind).order_by(WorkerEvent.id.desc()).limit(limit))
        )


def refusal_sink(db: Engine) -> RefusalSink:
    """A client refusal sink that records each refusal as a ``platform_refusal`` row."""

    def write(refusal: Refusal) -> None:
        record(
            db,
            Event(
                at=refusal.at,
                kind="platform_refusal",
                action=refusal.action,
                status=refusal.status,
                code=refusal.code,
                message=refusal.message,
                mandate_id=refusal.mandate_id,
                run_id=refusal.run_id,
                authorization_id=refusal.authorization_id,
            ),
        )

    return write
