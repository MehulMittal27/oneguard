"""Decisions no platform owns: the offline replay (D1, D2) and local step-up closing.

The replay feeds data-pack events (built by ``api/routes_dev.py`` through
``oneguard.replay.events``) to ``pipeline.decide_event`` with the same engine and ledger
as the live worker; only the event source differs (api-contract §1.2). Events are paced
``speed_ms`` apart, stamped with a fresh real-clock deadline, given the run's own
``context`` and stored in ``events_raw`` like live ones.

A replay step-up waits for the customer like a live one: C8 closes it here, and so does
its expiry at ``deadline_at`` (rules.md Q2), with nothing posted anywhere. A live
step-up is closed here only when no worker is connected, so a lapsed one still reads as
expired after a reload (api-contract §3.5).

All ledger calls run on one dedicated thread with one ``StoreLedger`` session, as in the
worker: a session is never used from two threads, and its view of the replay's rows is
never stale.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import secrets
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal, TypeVar

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from oneguard.api import models as api
from oneguard.engine.explain import expired_message
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import HistoryIndex, Policy
from oneguard.llm.provider import Provider
from oneguard.pipeline import HUMAN_WINDOW_S, PipelineContext, decide_event
from oneguard.replay.events import (
    DEADLINE_SECONDS,
    RunDecision,
    format_ts,
    with_run_context,
)
from oneguard.store.db import session
from oneguard.store.schema import EventRaw, Run
from oneguard.viseca.worker import NotAwaitingAnswer, WindowClosed

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_SPEED_MS = 2000


def live_ids(source_ids: list[str]) -> dict[str, str]:
    """Fresh live ids for one replay, as the platform assigns them per run."""
    return {source: f"rp_{secrets.token_hex(6)}" for source in source_ids}


@dataclass
class ReplayState:
    run_id: str
    scenario_id: str
    card_id: str
    customer_id: str
    policy: Policy
    events: list[dict[str, Any]]
    speed_ms: int
    started_at: datetime
    delivered: int = 0
    running: bool = True
    next_at: datetime | None = None
    decided: list[RunDecision] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    ctx: PipelineContext | None = None

    def status(self) -> api.ReplayStatus:
        return api.ReplayStatus(
            scenario_id=self.scenario_id,
            card_id=self.card_id,
            delivered=self.delivered,
            total=len(self.events),
            running=self.running,
            next_at=self.next_at if self.running else None,
            ledger_run_id=self.run_id,
            started_at=self.started_at,
            decided=len(self.decided),
            customer_id=self.customer_id,
        )


class OfflineRunner:
    """The offline replay and the local ledger it and C8 share."""

    def __init__(
        self,
        db: Engine,
        *,
        implementations: Mapping[str, Callable[..., Any]] | None = None,
        stubbed: frozenset[str] | None = None,
        human_window_s: int = HUMAN_WINDOW_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._implementations = implementations
        self._stubbed = stubbed
        self.human_window_s = human_window_s
        self._now = now
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oneguard-offline")
        self._ledger: StoreLedger | None = None
        self._state: ReplayState | None = None
        self._expiry: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # Ledger thread -----------------------------------------------------------------------

    async def _call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._pool, partial(fn, *args, **kwargs))

    def _ledger_for(self, history: HistoryIndex | None) -> StoreLedger:
        if self._ledger is None:
            self._ledger = StoreLedger(Session(self._db, expire_on_commit=False), history=history)
        elif history is not None:
            self._ledger.history = history
        return self._ledger

    # Replay ------------------------------------------------------------------------------

    def status(self) -> api.ReplayStatus | None:
        return self._state.status() if self._state else None

    def current(self) -> tuple[str, datetime] | None:
        """(run id, started at) of the latest replay this process started, if any."""
        return (self._state.run_id, self._state.started_at) if self._state else None

    async def restart(
        self,
        *,
        scenario_id: str,
        card_id: str,
        customer_id: str,
        policy: Policy,
        events: list[dict[str, Any]],
        history: HistoryIndex,
        provider: Provider | None,
        signals_enabled: bool,
        speed_ms: int | None,
    ) -> api.ReplayStatus:
        """Stop the running replay (its pending step-ups keep their windows) and start anew."""
        await self._stop_replay()
        state = ReplayState(
            run_id=f"replay-{secrets.token_hex(6)}",
            scenario_id=scenario_id,
            card_id=card_id,
            customer_id=customer_id,
            policy=policy,
            events=events,
            speed_ms=DEFAULT_SPEED_MS if speed_ms is None else speed_ms,
            started_at=self._now(),
        )
        state.next_at = state.started_at
        await asyncio.to_thread(self._save_run, state)
        ledger = await self._call(self._ledger_for, history)
        ctx = PipelineContext(
            policy=policy,
            ledger=ledger,
            history=history,
            run_id=state.run_id,
            provider=provider,
            signals_enabled=signals_enabled,
            human_window_s=self.human_window_s,
            implementations=self._implementations,
            stubbed=self._stubbed,
            now=self._now,
        )
        state.ctx = ctx
        self._state = state
        state.task = asyncio.create_task(self._run(state, ctx), name=f"replay-{state.run_id}")
        return state.status()

    async def finished(self) -> api.ReplayStatus | None:
        """Wait until the replay has delivered its last event; its step-ups stay open."""
        state = self._state
        if state is not None and state.task is not None:
            await asyncio.gather(state.task, return_exceptions=True)
        return self.status()

    def bind_policy(self, policy: Policy) -> None:
        """A tightened or revoked policy applies to the running replay's next purchase (T6)."""
        state = self._state
        if state is not None and state.ctx is not None and state.policy.mandate_id == policy.mandate_id:
            state.policy = policy
            state.ctx.policy = policy

    async def _stop_replay(self) -> None:
        state = self._state
        if state is not None and state.task is not None and not state.task.done():
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
            state.running = False
            await asyncio.to_thread(self._save_run, state)

    async def _run(self, state: ReplayState, ctx: PipelineContext) -> None:
        error: str | None = None
        try:
            for i, template in enumerate(state.events):
                if i:
                    state.next_at = self._now() + timedelta(milliseconds=state.speed_ms)
                    await asyncio.sleep(state.speed_ms / 1000)
                event = self._stamp(template, state)
                await asyncio.to_thread(self._save_event, state.run_id, event)
                async with self._lock:
                    _, _, decision = await self._call(decide_event, event, ctx)
                state.delivered += 1
                auth = event["authorization"]
                state.decided.append(
                    RunDecision(
                        authorization_id=decision.authorization_id,
                        timestamp=auth["timestamp"],
                        merchant_id=auth["merchant"]["merchant_id"],
                        billing_amount_chf=auth["billing_amount_chf"],
                        status=_status(decision),
                    )
                )
                if decision.status == "pending_human" and decision.deadline_at is not None:
                    self._schedule_expiry(decision.authorization_id, decision.deadline_at)
                await asyncio.to_thread(self._save_run, state)
        except asyncio.CancelledError:
            state.running = False
            raise
        except Exception as exc:
            log.exception("offline replay %s failed", state.run_id)
            error = f"{type(exc).__name__}: {exc}"
        state.running = False
        state.next_at = None
        await asyncio.to_thread(self._save_run, state, error)

    def _stamp(self, template: dict[str, Any], state: ReplayState) -> dict[str, Any]:
        """The event as the platform would queue it now: fresh deadline, run context."""
        now = self._now()
        event = copy.deepcopy(template)
        event["deadline_at"] = format_ts(now + timedelta(seconds=DEADLINE_SECONDS))
        event["runtime"]["received_at"] = format_ts(now)
        return with_run_context(event, state.decided)

    def _save_event(self, run_id: str, event: dict[str, Any]) -> None:
        auth = event["authorization"]
        with session(self._db) as s:
            if s.get(EventRaw, auth["authorization_id"]) is None:
                s.add(
                    EventRaw(
                        live_authorization_id=auth["authorization_id"],
                        run_id=run_id,
                        source_authorization_id=auth["source_authorization_id"],
                        received_at=datetime.fromisoformat(event["runtime"]["received_at"]),
                        deadline_at=datetime.fromisoformat(event["deadline_at"]),
                        event=event,
                    )
                )

    def _save_run(self, state: ReplayState, error: str | None = None) -> None:
        done = not state.running
        with session(self._db) as s:
            s.merge(
                Run(
                    run_id=state.run_id,
                    viseca_run_id=None,
                    kind="replay",
                    scenario_id=state.scenario_id,
                    mandate_id=state.policy.mandate_id,
                    card_id=state.card_id,
                    state="error" if error else ("done" if done else "running"),
                    delivered=state.delivered,
                    decided=len(state.decided),
                    pending_human=sum(1 for d in state.decided if d.status == "pending"),
                    total=len(state.events),
                    started_at=state.started_at,
                    finished_at=self._now() if done or error else None,
                    worker_last_poll_at=None,
                    last_error=error,
                )
            )

    # Closing step-ups --------------------------------------------------------------------

    async def resolve(
        self, live_id: str, decision: Literal["approve", "decline"], history: HistoryIndex | None = None
    ) -> LedgerEntry:
        """C8 for a step-up no platform owns. Raises KeyError, NotAwaitingAnswer, WindowClosed."""
        async with self._lock:
            ledger = await self._call(self._ledger_for, history)
            entry = await self._call(_fresh, ledger, live_id)
            if entry is None:
                raise KeyError(live_id)
            if entry.outcome != "step_up" or entry.final:
                raise NotAwaitingAnswer(f"{live_id} is not awaiting an answer")
            if entry.deadline_at is not None and self._now() >= entry.deadline_at:
                raise WindowClosed(f"the window for {live_id} closed at {entry.deadline_at}")
            resolved = await self._call(ledger.resolve, live_id, decision, "customer", self._now())
        self._cancel_expiry(live_id)
        self._mark(live_id, resolved)
        return resolved

    async def expire(self, live_id: str, history: HistoryIndex | None = None) -> bool:
        """Close an unanswered step-up (rules.md Q2). False if it was already closed."""
        async with self._lock:
            ledger = await self._call(self._ledger_for, history)
            entry = await self._call(_fresh, ledger, live_id)
            if entry is None or entry.outcome != "step_up" or entry.final:
                return False
            resolved = await self._call(
                ledger.resolve, live_id, "decline", "timeout", self._now(),
                message=expired_message(self.human_window_s),
            )
        self._cancel_expiry(live_id)
        self._mark(live_id, resolved)
        log.info("step-up %s expired unanswered; declined, reservation released", live_id)
        return True

    def _mark(self, live_id: str, entry: LedgerEntry) -> None:
        state = self._state
        if state is None:
            return
        for i, d in enumerate(state.decided):
            if d.authorization_id == live_id:
                status = "approved" if entry.uncertain_outcome == "approved" else "declined"
                state.decided[i] = RunDecision(d.authorization_id, d.timestamp, d.merchant_id, d.billing_amount_chf, status)

    def _schedule_expiry(self, live_id: str, deadline: datetime) -> None:
        self._cancel_expiry(live_id)
        self._expiry[live_id] = asyncio.create_task(self._expire_at(live_id, deadline))

    def _cancel_expiry(self, live_id: str) -> None:
        task = self._expiry.pop(live_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _expire_at(self, live_id: str, deadline: datetime) -> None:
        try:
            await asyncio.sleep(max(0.0, (deadline - self._now()).total_seconds()))
            await self.expire(live_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("expiry of %s failed", live_id)

    async def stop(self) -> None:
        await self._stop_replay()
        tasks = list(self._expiry.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._expiry.clear()
        if self._ledger is not None:
            await self._call(self._ledger.session.close)
        self._pool.shutdown(wait=False)


def _fresh(ledger: StoreLedger, live_id: str) -> LedgerEntry | None:
    """The stored entry, re-read: another writer (the worker) may have closed it."""
    from oneguard.store.schema import Decision

    row = ledger.session.get(Decision, live_id)
    if row is not None:
        ledger.session.refresh(row)
    return ledger.get(live_id)


def _status(decision: api.Decision) -> str:
    """The platform's word for a decision in ``context.recent_authorizations``."""
    if decision.decision == "approved":
        return "approved"
    if decision.decision == "stopped":
        return "declined"
    if decision.status == "pending_human":
        return "pending"
    return "approved" if decision.uncertain_outcome == "approved" else "declined"
