"""What every route shares: the store, history, providers, the worker and the offline
runner, built once by the app's lifespan (``api/app.py``) and kept on ``app.state``.

Every store call goes through ``Services.db`` and every model call through a bounded
wait, so a slow dependency answers with a fast ``503``/``504``, never a hang
(api-contract Appendix A).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from oneguard.api import policies, queries
from oneguard.api.errors import ApiError, not_found, upstream_unavailable
from oneguard.api.offline import OfflineRunner
from oneguard.engine import stubs
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import HistoryIndex
from oneguard.llm.provider import Provider
from oneguard.store.schema import Mandate
from oneguard.viseca.client import VisecaClient, VisecaError
from oneguard.viseca.worker import NotAwaitingAnswer, VisecaWorker, WindowClosed

log = logging.getLogger(__name__)

T = TypeVar("T")

SignalsBackend = Literal["off", "keywords", "laya"]
DB_TIMEOUT_S = 10.0
VISECA_TIMEOUT_S = 15.0
"""Longest wait for a platform call made for a customer request (the client's own read
timeout is 10 s; this bounds the whole call whatever the transport does)."""
COMPILE_TIMEOUT_S = 10.0
"""C1: the compiler's own LLM timeout is 8 s (§3.2); lint and dry-run come on top."""
LAPSE_GRACE_S = 1.0
"""A live step-up is closed by a read only this long after its deadline, so the worker's
own expiry (and the platform's) goes first."""


@dataclass(frozen=True)
class ScenarioBinding:
    """A scenario behind a customer: which card it runs on (operator data, C12, D3, D8).

    ``source``: ``pack`` (the local data pack's authorities) or who at the platform said
    so (``bootstrap``, ``run``, ``authorization``; ``store.schema.ScenarioProfile``).
    """

    scenario_id: str
    card_id: str
    profile_id: str | None = None
    source: str = "pack"


@dataclass
class Services:
    db_engine: Engine
    history: HistoryIndex
    provider: Provider
    provider_name: str
    signals_backend: SignalsBackend
    offline: OfflineRunner
    client: VisecaClient | None = None
    worker: VisecaWorker | None = None
    scenarios: dict[str, list[ScenarioBinding]] = field(default_factory=dict)
    """customer id → the local pack's scenarios on their cards; ``bindings`` adds the platform's."""
    implementations: Mapping[str, Callable[..., Any]] | None = None
    stubbed: frozenset[str] | None = None
    model_loaded: bool = False
    model_loading: bool = False
    """The ``laya`` model is loading in the background (the lifespan); keywords answer meanwhile."""
    models_enabled: bool | None = None
    """D5 chaos toggle; None until an operator sets it."""
    worker_error: str | None = None
    live_started: dict[str, datetime] = field(default_factory=dict)
    """Viseca run id → real time D3 started it (before the worker has stored its row)."""
    db_timeout_s: float = DB_TIMEOUT_S
    viseca_timeout_s: float = VISECA_TIMEOUT_S
    compile_timeout_s: float = COMPILE_TIMEOUT_S
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    policy_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Serialises C2, C4, C5 so one draft is never confirmed twice."""

    @property
    def functions(self) -> Mapping[str, Callable[..., Any]]:
        return self.implementations if self.implementations is not None else stubs.ACTIVE

    # Scenario bindings (operator data) ---------------------------------------------------

    async def bindings(self) -> dict[str, list[ScenarioBinding]]:
        """customer id → the scenarios that run on their cards, by scenario id.

        The local pack's bindings, then every one the platform stated (the worker's
        ``scenario_profiles``); the platform wins for a scenario both name.
        """
        stored = await self.db(queries.scenario_profiles, self.db_engine)
        by_scenario = {b.scenario_id: (customer, b) for customer, bs in self.scenarios.items() for b in bs}
        for row in stored:
            by_scenario[row.scenario_id] = (
                row.customer_id,
                ScenarioBinding(row.scenario_id, row.card_id, row.profile_id, row.source),
            )
        found: dict[str, list[ScenarioBinding]] = {}
        for scenario_id in sorted(by_scenario):
            customer, binding = by_scenario[scenario_id]
            found.setdefault(customer, []).append(binding)
        return found

    # Models (D5) -------------------------------------------------------------------------

    def live_models(self) -> bool:
        """Soft signals and tier 2 for live runs: on unless signals are off or D5 says so."""
        return self.signals_backend != "off" if self.models_enabled is None else self.models_enabled

    def active_signals(self) -> SignalsBackend:
        """The detector answering now: ``keywords`` stands in for ``laya`` until its model loads."""
        return "keywords" if self.signals_backend == "laya" and not self.model_loaded else self.signals_backend

    def replay_models(self) -> bool:
        """Off by default in the offline replay (api-contract §3.7); D5 turns them on."""
        return bool(self.models_enabled)

    def decision_provider(self, enabled: bool) -> Provider | None:
        return self.provider if enabled else None

    def set_models(self, enabled: bool) -> None:
        self.models_enabled = enabled
        if self.worker is not None:
            self.worker.set_models(signals_enabled=enabled, provider=self.decision_provider(enabled))

    def bind_mandate(self, row: Mandate) -> None:
        """Decide with this mandate's policy from the next purchase on, live and in a replay."""
        rules, flags = policies.load_rules(row.rules, row.checks)
        policy = policies.policy_of(row.mandate_id, row.status, row.instruction, rules, flags, row.uncertainty_policy)
        self.offline.bind_policy(policy)
        if self.worker is not None and row.viseca_mandate_id:
            self.worker.bind_policy(row.viseca_mandate_id, policy)

    # Bounded calls ------------------------------------------------------------------------

    async def db(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """A store call in a thread, bounded by ``db_timeout_s``."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), self.db_timeout_s)
        except TimeoutError:
            raise upstream_unavailable("The database did not answer in time.") from None
        except SQLAlchemyError as exc:
            log.exception("store call %s failed", getattr(fn, "__name__", fn))
            raise upstream_unavailable("The database could not be reached.") from exc

    async def platform(self, call: Awaitable[T]) -> T:
        """Await a platform call, bounded by ``viseca_timeout_s`` (VisecaError on timeout)."""
        try:
            return await asyncio.wait_for(call, self.viseca_timeout_s)
        except TimeoutError:
            raise VisecaError(None, "upstream_unavailable", "the platform did not answer in time") from None

    async def viseca(self, call: Awaitable[T], action: str) -> T:
        """Await a Viseca call; a platform failure is a 503 and nothing is recorded."""
        try:
            return await self.platform(call)
        except VisecaError as exc:
            log.warning("Viseca %s failed: %s", action, exc)
            raise upstream_unavailable(
                f"The payment platform did not accept the {action}; nothing was changed.",
                {"platform_status": exc.status, "platform_code": exc.code},
            ) from None

    # Step-ups ----------------------------------------------------------------------------

    async def resolve(self, live_id: str, decision: Literal["approve", "decline"]) -> LedgerEntry:
        """C8: the customer's answer, through whoever owns the step-up.

        A live step-up goes through the worker, which asks the platform first and records
        only what it accepted. A replay step-up is closed by the offline runner.
        """
        stored = await self.db(queries.decision, self.db_engine, live_id)
        if stored is None:
            raise not_found(f"No purchase {live_id}.")
        try:
            if stored.run_kind == "replay":
                return await self.offline.resolve(live_id, decision, self.history)
            if self.worker is None:
                raise upstream_unavailable(
                    "The payment platform is not connected, so the answer could not be passed on; nothing was recorded."
                )
            return await self.platform(self.worker.resolve_by_customer(live_id, decision))
        except KeyError:
            raise not_found(f"No purchase {live_id}.") from None
        except WindowClosed:
            await self._close_lapsed(stored)
            raise ApiError(
                409, "window_closed", "The time to answer has run out; nothing was approved."
            ) from None
        except NotAwaitingAnswer:
            raise ApiError(409, "not_awaiting_answer", "This purchase is not waiting for an answer.") from None
        except VisecaError as exc:
            if exc.status == 409:
                raise ApiError(
                    409,
                    "not_awaiting_answer",
                    "The payment platform has already closed this purchase.",
                    {"platform_code": exc.code},
                ) from None
            log.warning("resolve of %s not accepted by Viseca: %s", live_id, exc)
            raise upstream_unavailable(
                "The payment platform did not accept the answer; nothing was recorded.",
                {"platform_status": exc.status, "platform_code": exc.code},
            ) from None

    async def close_lapsed(self, decisions: list[queries.StoredDecision]) -> bool:
        """Close every pending step-up whose window has passed (§3.5). True if any closed."""
        now = self.now()
        closed = False
        for stored in decisions:
            entry = stored.entry
            if entry.outcome != "step_up" or entry.final or entry.deadline_at is None:
                continue
            live = stored.run_kind != "replay"
            grace = LAPSE_GRACE_S if live and self.worker is not None else 0.0
            if (now - entry.deadline_at).total_seconds() >= grace:
                closed |= await self._close_lapsed(stored)
        return closed

    async def _close_lapsed(self, stored: queries.StoredDecision) -> bool:
        live_id = stored.entry.live_authorization_id
        try:
            if stored.run_kind != "replay" and self.worker is not None:
                return await self.worker.expire(live_id)
            return await self.offline.expire(live_id, self.history)
        except Exception:
            log.exception("could not close the lapsed step-up %s", live_id)
            return False
