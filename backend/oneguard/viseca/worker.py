"""The long-poll worker between the Viseca sandbox and the pipeline.

Runs as an asyncio task inside the API process (docs/architecture.md Runtime). One
loop, never blocked by a human:

- ``start``: ``GET /v1/bootstrap`` (``limits``: human window, decision deadline, long-poll
  cap) and ``GET /v1/reference-data``. Every reference table it serves under ``tables``
  (customers, accounts, cards, merchants, items, fx rates, the scenario catalogue) is
  upserted into the store in one transaction when it differs from the stored rows, with
  per-table counts logged (``seed.sync_served``); served-only customers, cards and
  scenarios then exist for the API. The sandbox serves no history-file hash, so the
  file is downloaded from ``/v1/reference-data/authorization-history.csv`` and hashed; if
  it differs from the one the seed checked (``data/metadata.json``),
  ``authorization_history`` is re-seeded from it and that is logged loudly. The served
  ``tables.fx_rates`` must equal ``facts.FX_TO_CHF`` (the rates every CHF amount is
  converted with); a mismatch is logged loudly and keeps ``ok`` false (``/healthz`` degraded).
- loop: long-poll ``/v1/decision-requests/next?wait=25``. 204 → read the progress of
  every tracked run and the event feed, poll again. 200 → validate ``data`` against the
  event schema, remember the live → source id map (and the live related id), store the
  full event in ``events_raw``, reconcile ``context.approved_spend_in_period_chf``
  against the ledger, run ``pipeline.decide_event`` within ``ONEGUARD_ENGINE_BUDGET_MS``
  and POST the decision before ``deadline_at``. An engine still unfinished just before
  ``deadline_at`` gets a pending ``step_up`` (``unevaluable``) posted in its place, which
  then waits on the customer like any other step-up (rules.md D3).
- redelivery of a known live id posts the stored decision again and counts nothing (M7).
  A step-up we posted comes back on every poll with envelope ``status:
  "pending_step_up"`` until it is resolved or expires: nothing is posted for it (the
  platform would answer 409 ``step_up_resolution_required``) and the loop pauses
  ``waiting_step_up_pause_s`` so it does not spin.
- a ``step_up`` accepted by Viseca gets ``deadline_at`` = the reply's
  ``step_up_expires_at`` (accepted time + the bootstrap human window) and an expiry task.
  The loop never waits for it. Unanswered at the deadline → ``POST /resolve`` ``decline``
  with the timeout message, ``resolved_by: timeout``, and the ledger marks it expired and
  releases the reservation (rules.md Q2, api-contract §3.5). The platform expires it
  itself at the same moment, so the expiry reads the platform's state first
  (``GET /v1/authorizations?run_id=``): already final there → record that, post nothing;
  still pending → ``/resolve``, and on a 409 read again and record what it says.
- at most one ``/resolve`` is ever sent per live id (customer or timeout), across
  redelivery, retries and a rescheduled expiry.
- a customer answer (C8) goes through ``resolve_by_customer``; it and the expiry are
  serialised, so exactly one of them closes a step-up.
- ``revoke``: our policy flips to revoked at once, then ``DELETE /v1/mandates/{id}``;
  anything delivered afterwards is declined with ``card_or_authority_inactive``
  (rules.md T6, Q6; pipeline step 3).
- after each decision the feed ``GET /v1/events?since=<cursor>`` is compared with the
  ledger; a mismatch becomes an ``info`` evidence row on the next decision. Once a page is
  processed its ``next_cursor`` is stored (``worker_state``); ``start`` resumes from the
  stored cursor, so a restart never re-scans the team-wide feed (0 only on first boot).

``status()`` is what ``/healthz`` reports: state, last poll, events cursor, runs.

All ledger and pipeline calls run on one dedicated thread, so a SQL ledger session is
never used from two threads at once. Each of them (a decision, a resolution, a read) runs
in its own short ledger session (``ScopedStoreLedger``), closed afterwards; the worker
never holds a pooled connection for its lifetime. Store writes (``events_raw``, ``runs``)
use their own short sessions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from oneguard import __version__
from oneguard.api import models as api
from oneguard.engine.explain import expired_message
from oneguard.engine.facts import FX_TO_CHF
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import Ledger, LedgerEntry
from oneguard.engine.types import (
    EvidenceRow,
    HistoryIndex,
    LedgerView,
    Policy,
    Rule,
    RuleKind,
)
from oneguard.llm.provider import Provider
from oneguard.pipeline import (
    PipelineContext,
    budget_ms_from_env,
    decide_event,
    period_days_of,
    to_api_decision,
)
from oneguard.store import seed as seed_module
from oneguard.store.db import get_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import EventRaw, Mandate, Run, WorkerState
from oneguard.viseca.client import VisecaClient, VisecaError, cap
from oneguard.viseca.schema import event_errors

log = logging.getLogger(__name__)

T = TypeVar("T")

POLL_WAIT_S = 25.0
DEFAULT_HUMAN_WINDOW_S = 120.0
WAITING_STEP_UP_PAUSE_S = 0.5
"""Pause after the platform serves a step-up that still waits for its answer (it does so on
every poll, without the long-poll wait)."""
STEP_UP_WAITING = "pending_step_up"
"""Envelope ``status`` of a step-up we posted that awaits ``/resolve``."""
STOP_DRAIN_S = 5.0
"""How long ``stop`` waits for the engine thread to finish its current unit of work."""
POST_MARGIN_S = 0.5
"""Time kept free before ``deadline_at`` to POST the step-up when the engine overruns its budget."""
POST_RETRY_DELAYS_S = (0.2, 0.5, 1.0)
LOOP_BACKOFF_MAX_S = 10.0
RECONCILE_TOLERANCE_CHF = Decimal("0.005")
HISTORY_FILE = "authorization_history.csv"
EVENTS_CURSOR_KEY = "events_cursor"

TIMEOUT_MESSAGE = "No answer within {seconds} s; nothing was approved"
"""rules.md Q2 / api-contract §3.1, §3.5, with the human window from /v1/bootstrap."""
CUSTOMER_MESSAGES = {
    "approve": "The customer confirmed this purchase.",
    "decline": "The customer declined this purchase.",
}
OVERRUN_MESSAGE = "We couldn't check this purchase in time; please review it"
OVERRUN_DECLINE_MESSAGE = (
    "Declined: we could not finish checking this purchase in time, so nothing was approved."
)
FALLBACK_MESSAGE = (
    "Declined: we could not finish checking this purchase before its deadline, "
    "so nothing was approved."
)
INVALID_EVENT_MESSAGE = (
    "Declined: the purchase request was incomplete, so it could not be checked "
    "and nothing was approved."
)

_DONE_STATES = {"completed", "complete", "done", "finished"}
_ERROR_STATES = {"failed", "error", "errored", "cancelled", "canceled", "aborted"}
_FEED_FINAL = {
    "approved": "approved",
    "approve": "approved",
    "declined": "declined",
    "decline": "declined",
    "expired": "declined",
    "cancelled": "declined",
    "canceled": "declined",
}
_ACCEPTED_KEYS = ("accepted_at", "decided_at", "recorded_at", "created_at", "updated_at")
"""Fallback when a step-up reply lacks ``step_up_expires_at``: its accepted time."""
_TOTAL_KEYS = ("generated_event_count", "total", "total_events", "event_count", "events_total")


def timeout_message(window_s: float) -> str:
    """The expiry message; exactly the docs' wording for the default 120 s window."""
    seconds = int(window_s) if float(window_s).is_integer() else f"{window_s:g}"
    return TIMEOUT_MESSAGE.format(seconds=seconds)


class NotAwaitingAnswer(Exception):
    """The authorization is not a pending step-up (C8 409 ``not_awaiting_answer``)."""


class WindowClosed(NotAwaitingAnswer):
    """The human window has passed (C8 409 ``window_closed``)."""


# Tolerant readers for platform payloads whose exact shape the docs do not fix -------------


def walk_json(obj: Any) -> Iterator[tuple[str, Any, dict[str, Any]]]:
    """Every ``(key, value, parent)`` in ``obj``, breadth first (top-level keys win)."""
    queue: deque[Any] = deque([obj])
    while queue:
        node = queue.popleft()
        if isinstance(node, dict):
            for key, value in node.items():
                yield str(key), value, node
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(node, list):
            queue.extend(x for x in node if isinstance(x, (dict, list)))


def first_value(obj: Any, *keys: str) -> Any:
    for key, value, _ in walk_json(obj):
        if key in keys and value is not None:
            return value
    return None


@dataclass(frozen=True)
class ServedProfile:
    """The fixture profile ``/v1/bootstrap`` names for the team."""

    scenario_id: str
    customer_id: str
    card_id: str


def served_profile(bootstrap: Any) -> ServedProfile | None:
    """The bootstrap ``profile``'s scenario, customer and card, if it names all three.

    Live shape: ``profile{profile_id, scenario_id, profile_context{customer_id, account_id,
    card_id}, customer{...}, account{...}, card{...}}``.
    """
    profile = bootstrap.get("profile") if isinstance(bootstrap, dict) else None
    if not isinstance(profile, dict):
        return None

    def part(key: str) -> dict[str, Any]:
        value = profile.get(key)
        return value if isinstance(value, dict) else {}

    context = part("profile_context")
    scenario = profile.get("scenario_id")
    customer = context.get("customer_id") or part("customer").get("customer_id")
    card = context.get("card_id") or part("card").get("card_id")
    if not all(isinstance(v, str) and v for v in (scenario, customer, card)):
        return None
    return ServedProfile(scenario_id=scenario, customer_id=customer, card_id=card)


def seconds_setting(
    obj: Any, must: tuple[str, ...], any_of: tuple[str, ...], default: float | None
) -> float | None:
    """A duration in seconds from the first numeric key naming all of ``must`` and one of
    ``any_of`` (``_ms`` and ``_minutes`` suffixes converted)."""
    for key, value, _ in walk_json(obj):
        k = key.lower()
        if not (all(m in k for m in must) and any(a in k for a in any_of)):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            continue
        if k.endswith("_ms") or "millis" in k:
            return value / 1000
        if k.endswith(("_minutes", "_min", "_mins")):
            return value * 60.0
        return float(value)
    return default


def positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def find_history_metadata(reference: Any) -> dict[str, Any] | None:
    """Served history-file metadata carrying a SHA-256, if any.

    The live sandbox's ``history`` block (``path``, ``rows``, ``format``) has none; this
    stays tolerant in case one is added.
    """
    queue: deque[tuple[str, Any]] = deque([("", reference)])
    while queue:
        key, node = queue.popleft()
        if isinstance(node, dict):
            if "sha256" in node:
                names = [key, *(v for v in node.values() if isinstance(v, str))]
                if any("authorization_history" in n.lower().replace("-", "_") for n in names):
                    return node
                if "history" in key.lower():
                    return node
            queue.extend((str(k), v) for k, v in node.items() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend((key, v) for v in node if isinstance(v, (dict, list)))
    return None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value).strip())
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def fx_rate_mismatches(reference: Any, expected: Mapping[str, Decimal] = FX_TO_CHF) -> list[str]:
    """How the served ``tables.fx_rates`` differ from ``expected``; empty when they agree.

    Rows are ``{from_currency, to_currency, rate, ...}`` as in ``data/fx_rates.csv``. A rate
    served as a number or a string is read as a decimal and compared exactly (``0.95`` equals
    ``0.950000``); no tolerance. A table that is missing or unreadable is a mismatch.
    """
    tables = reference.get("tables") if isinstance(reference, dict) else None
    rows = tables.get("fx_rates") if isinstance(tables, dict) else None
    if not isinstance(rows, list):
        return ["no fx_rates table served"]
    problems: list[str] = []
    served: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, dict):
            problems.append(f"unreadable row {row!r}")
            continue
        currency, target, raw = row.get("from_currency"), row.get("to_currency"), row.get("rate")
        if target != "CHF":
            problems.append(f"{currency}: converts to {target!r}, not CHF")
            continue
        rate = _decimal(raw)
        if rate is None:
            problems.append(f"{currency}: unreadable rate {raw!r}")
            continue
        if currency in served:
            problems.append(f"{currency}: served twice")
        served[str(currency)] = rate
    for currency in sorted(set(expected) | set(served)):
        want, got = expected.get(currency), served.get(currency)
        if want is None:
            problems.append(f"{currency}: served {got}, the engine has no rate")
        elif got is None:
            if not any(p.startswith(f"{currency}:") for p in problems):
                problems.append(f"{currency}: not served, the engine uses {want}")
        elif got != want:
            problems.append(f"{currency}: served {got}, the engine uses {want}")
    return problems


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# Policy from the platform's mandate snapshot ----------------------------------------------


def _rule_kind(raw: Mapping[str, Any]) -> RuleKind:
    field_name = str(raw["field"])
    if raw.get("scope") == "period":
        return "period"
    if "billing_amount" in field_name:
        return "amount"
    if field_name.startswith("merchant."):
        return "merchant"
    if field_name.startswith("items["):
        return "item"
    if field_name.startswith("order."):
        return "terms"
    return "other"


def _rule_text(raw: Mapping[str, Any]) -> str:
    value = raw["value"]
    shown = ", ".join(value) if isinstance(value, list) else str(value)
    text = f"{raw['field']} {raw['operator']} {shown}"
    if raw.get("currency"):
        text += f" {raw['currency']}"
    if raw.get("scope") == "period" and raw.get("period_days"):
        text += f" across any {raw['period_days']} days"
    return text


def policy_from_snapshot(mandate: Mapping[str, Any]) -> Policy:
    """A Policy from the event's ``mandate`` snapshot (hard_rules as stored at Viseca).

    Used only when no confirmed policy is bound for that mandate. The convenience fields
    are restated from the rules where the field vocabulary allows (api-contract §3.3).
    """
    rules: list[Rule] = []
    allowed: list[str] | None = None
    blocked: list[str] | None = None
    known_shop = False
    shop_type: str | None = None
    for i, raw in enumerate(mandate.get("hard_rules") or [], start=1):
        rules.append(
            Rule(
                id=f"hard_rule_{i}",
                field=raw["field"],
                operator=raw["operator"],
                value=raw["value"],
                currency=raw.get("currency"),
                scope=raw.get("scope"),
                period_days=raw.get("period_days"),
                text=_rule_text(raw),
                source="exact",
                kind=_rule_kind(raw),
            )
        )
        field_name, op, value = raw["field"], raw["operator"], raw["value"]
        if field_name == "items[].item_category" and isinstance(value, list):
            if op == "in":
                allowed = sorted(set(allowed or value) & set(value))
            elif op == "not_in":
                blocked = sorted(set(blocked or []) | set(value))
        elif field_name == "merchant.familiar_on_card" and op == "=" and str(value) == "true":
            known_shop = True
        elif field_name == "merchant.merchant_category" and op == "=" and isinstance(value, str):
            shop_type = value
    return Policy(
        mandate_id=mandate["mandate_id"],
        status=mandate.get("status", "active"),
        instruction=mandate.get("instruction", ""),
        rules=rules,
        uncertainty_policy=mandate.get("uncertainty_policy", "ask"),
        allowed_item_categories=allowed,
        blocked_item_categories=blocked,
        requires_known_shop=known_shop,
        shop_type=shop_type,
    )


class ScopedStoreLedger:
    """P2's ``StoreLedger`` on short sessions: one per unit of work, closed afterwards.

    ``scope()`` opens a session and binds a ``StoreLedger`` to it for every call made
    inside it on that thread; a call outside any scope gets a session of its own. The
    worker runs each decision, resolution and read in one scope on its engine thread, so a
    pooled connection (5 per process on Postgres) is held only while that work runs.
    """

    def __init__(self, db: Engine, history: HistoryIndex | None = None) -> None:
        self.db = db
        self.history = history
        self._local = threading.local()
        self._lock = threading.Lock()
        self._open: set[Session] = set()

    @contextmanager
    def scope(self) -> Iterator[StoreLedger]:
        current: StoreLedger | None = getattr(self._local, "ledger", None)
        if current is not None:
            yield current
            return
        db_session = Session(self.db, expire_on_commit=False)
        with self._lock:
            self._open.add(db_session)
        self._local.ledger = StoreLedger(db_session, history=self.history)
        try:
            yield self._local.ledger
        finally:
            self._local.ledger = None
            with self._lock:
                self._open.discard(db_session)
            db_session.close()

    @property
    def open_sessions(self) -> int:
        with self._lock:
            return len(self._open)

    def close(self) -> None:
        """Close any session still open: a unit of work cut short when the worker stops."""
        with self._lock:
            sessions, self._open = list(self._open), set()
        for db_session in sessions:
            db_session.close()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or getattr(StoreLedger, name, None) is None:
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            with self.scope() as ledger:
                return getattr(ledger, name)(*args, **kwargs)

        return call


def default_ledger(db: Engine, history: HistoryIndex) -> Ledger:
    """P2's store-backed ledger (``engine/ledger.py``) on short sessions."""
    return cast(Ledger, ScopedStoreLedger(db, history=history))


def platform_result(item: Mapping[str, Any]) -> tuple[Literal["approve", "decline"], Literal["customer", "timeout"]] | None:
    """The final answer ``GET /v1/authorizations`` shows for a step-up, or None while it waits.

    ``approved`` is the customer's approval; ``declined`` is the customer's decline when
    ``decision_source`` says ``customer``, else the platform's timeout.
    """
    status = str(item.get("status") or "").lower()
    decision = item.get("decision")
    source = item.get("decision_source") or (decision.get("decision_source") if isinstance(decision, dict) else None)
    if status == "approved":
        return "approve", "customer"
    if status in ("declined", "expired", "cancelled", "canceled"):
        return "decline", "customer" if source == "customer" else "timeout"
    return None


class OverrunClaims:
    """Which decision the ledger keeps for a live id when the engine overruns: first claim wins.

    The worker claims a live id with its overrun decision before posting it; the
    engine's own ``record`` then stores that decision instead of its late result. If the
    engine recorded first, the claim fails and the engine's decision is posted. Shared
    between the event loop and the engine thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: dict[str, LedgerEntry] = {}
        self._engine_recorded: set[str] = set()

    def claim(self, entry: LedgerEntry) -> bool:
        with self._lock:
            if entry.live_authorization_id in self._engine_recorded:
                return False
            self._claimed[entry.live_authorization_id] = entry
            return True

    def for_engine(self, entry: LedgerEntry) -> LedgerEntry:
        """The entry the engine may record: the overrun decision if one was claimed."""
        with self._lock:
            claimed = self._claimed.pop(entry.live_authorization_id, None)
            if claimed is None:
                self._engine_recorded.add(entry.live_authorization_id)
            return claimed or entry

    def take(self, live_id: str) -> LedgerEntry | None:
        """The claimed overrun decision the engine has not recorded yet."""
        with self._lock:
            return self._claimed.pop(live_id, None)

    def forget(self, live_id: str) -> None:
        with self._lock:
            self._engine_recorded.discard(live_id)


class ClaimedLedger:
    """The ledger as the engine sees it: ``record`` honours an overrun claim."""

    def __init__(self, ledger: Ledger, claims: OverrunClaims) -> None:
        self._ledger = ledger
        self._claims = claims

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        return self._ledger.record(self._claims.for_engine(entry))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)


# Status ------------------------------------------------------------------------------------


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    viseca_run_id: str
    scenario_id: str | None
    mandate_id: str | None
    card_id: str | None
    state: Literal["starting", "running", "done", "error"]
    delivered: int
    decided: int
    pending_human: int
    total: int
    redeliveries: int
    last_error: str | None
    recorded_state: Literal["starting", "running", "done", "error"] | None = None
    """``state`` of the committed ``runs`` row (None before the first write); lags ``state``
    until the write that follows a change has committed."""


class WorkerStatus(BaseModel):
    """What ``/healthz`` reports about the worker."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["stopped", "starting", "polling", "degraded"]
    ok: bool
    last_poll_at: datetime | None
    events_cursor: int | str
    human_window_s: float
    decision_deadline_s: float | None
    pending_step_ups: int
    history_reseeded: bool
    fx_rates_match: bool | None
    """Served ``tables.fx_rates`` equal ``facts.FX_TO_CHF``; None until checked. False keeps
    ``ok`` false (``state`` stays the loop's own)."""
    fx_rates_mismatch: list[str]
    last_error: str | None
    runs: list[RunStatus]


@dataclass
class RunState:
    viseca_run_id: str
    run_id: str
    started_at: datetime
    scenario_id: str | None = None
    viseca_mandate_id: str | None = None
    mandate_id: str | None = None
    card_id: str | None = None
    ctx: PipelineContext | None = None
    state: Literal["starting", "running", "done", "error"] = "starting"
    total: int = 0
    live_ids: list[str] = field(default_factory=list)
    decided: set[str] = field(default_factory=set)
    pending: set[str] = field(default_factory=set)
    redeliveries: int = 0
    platform_done: bool = False
    finished_at: datetime | None = None
    last_error: str | None = None
    recorded_state: Literal["starting", "running", "done", "error"] | None = None
    """``state`` as last committed to the ``runs`` row."""
    recorded_final: asyncio.Event = field(default_factory=asyncio.Event)
    """Set once a ``runs`` row with state done or error has committed."""

    def status(self) -> RunStatus:
        return RunStatus(
            run_id=self.run_id,
            viseca_run_id=self.viseca_run_id,
            scenario_id=self.scenario_id,
            mandate_id=self.mandate_id,
            card_id=self.card_id,
            state=self.state,
            delivered=len(self.live_ids),
            decided=len(self.decided),
            pending_human=len(self.pending),
            total=max(self.total, len(self.live_ids)),
            redeliveries=self.redeliveries,
            last_error=self.last_error,
            recorded_state=self.recorded_state,
        )


DecisionListener = Callable[[api.Decision], Any]
HandledListener = Callable[[str], Any]


# The worker --------------------------------------------------------------------------------


class VisecaWorker:
    """Long-polls Viseca, decides every request through the pipeline, closes step-ups."""

    def __init__(
        self,
        client: VisecaClient,
        *,
        db: Engine | None = None,
        history: HistoryIndex | None = None,
        ledger: Ledger | None = None,
        provider: Provider | None = None,
        signals_enabled: bool = False,
        budget_ms: int | None = None,
        poll_wait_s: float = POLL_WAIT_S,
        waiting_step_up_pause_s: float = WAITING_STEP_UP_PAUSE_S,
        implementations: Mapping[str, Callable[..., Any]] | None = None,
        stubbed: frozenset[str] | None = None,
        data_dir: Path = seed_module.DATA_DIR,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self._db_engine = db or get_engine()
        self._history = history
        self._ledger = ledger
        self._provider = provider
        self._signals_enabled = signals_enabled
        self._budget_ms = budget_ms if budget_ms is not None else budget_ms_from_env()
        self._poll_wait_s = poll_wait_s
        self._waiting_step_up_pause_s = waiting_step_up_pause_s
        self._implementations = implementations
        self._stubbed = stubbed
        self._data_dir = data_dir
        self._now = now

        self.human_window_s = DEFAULT_HUMAN_WINDOW_S
        self.decision_deadline_s: float | None = None
        self.bootstrap: dict[str, Any] | None = None
        self.reference_data: dict[str, Any] | None = None
        self.history_reseeded = False
        self.served_tables: list[seed_module.TableSync] = []
        """What the start-up pack check did to each served reference table."""
        self.served_history_sha256: str | None = None
        """SHA-256 of the history file Viseca serves, once checked at start."""
        self.fx_rates_mismatch: list[str] | None = None
        """How the served fx rates differ from ``FX_TO_CHF`` (empty: equal); None until checked."""

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oneguard-engine")
        self._task: asyncio.Task[None] | None = None
        self._state: Literal["stopped", "starting", "polling", "degraded"] = "stopped"
        self._failures = 0
        self._last_poll_at: datetime | None = None
        self._last_error: str | None = None
        self._cursor: int | str = 0
        self._runs: dict[str, RunState] = {}
        self._run_of: dict[str, RunState] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._policies: dict[str, Policy] = {}
        self._revoked: set[str] = set()
        self._expiry: dict[str, asyncio.Task[None]] = {}
        self._claims = OverrunClaims()
        self._run_write_lock = threading.Lock()
        """``_save_run`` runs on several threads; ``merge`` is not an atomic upsert."""
        self._resolution_lock = asyncio.Lock()
        self._resolve_sent: set[str] = set()
        """Live ids a ``/resolve`` was sent for: never a second one."""
        self._feed_mismatches: list[EvidenceRow] = []
        self._feed_seen: set[tuple[str, str]] = set()
        self._listeners: list[DecisionListener] = []
        self._handled_listeners: list[HandledListener] = []
        self._warned_no_set_deadline = False
        self.source_ids: dict[str, str] = {}
        """live authorization id → source ``AU…`` id (offline parity)."""
        self.related_ids: dict[str, str] = {}
        """live authorization id → the live id Viseca rewrote ``related_authorization_id`` to."""

    # Public API --------------------------------------------------------------------------

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            raise RuntimeError("worker not started")
        return self._ledger

    @property
    def history(self) -> HistoryIndex:
        if self._history is None:
            raise RuntimeError("worker not started")
        return self._history

    @property
    def events_cursor(self) -> int | str:
        return self._cursor

    def add_listener(self, listener: DecisionListener) -> None:
        """Called with the API ``Decision`` after every posted decision and resolution."""
        self._listeners.append(listener)

    def add_handled_listener(self, listener: HandledListener) -> None:
        """Called with the live authorization id once a delivered request is fully handled.

        At that point its decision is posted and in the ledger (the ledger's unit of work
        closed), and its ``events_raw`` and ``runs`` rows have committed; only call summaries
        (``viseca_calls``) may still be on their way (``client.drain``). Not called for a
        request that failed to be handled.
        """
        self._handled_listeners.append(listener)

    def bind_policy(self, viseca_mandate_id: str, policy: Policy) -> None:
        """The confirmed policy (typed rules) behind a Viseca ``TM…`` mandate."""
        if viseca_mandate_id in self._revoked:
            policy = policy.model_copy(update={"status": "revoked"})
        self._policies[viseca_mandate_id] = policy

    def set_models(self, *, signals_enabled: bool, provider: Provider | None) -> None:
        """Soft signals and the tier-2 provider for every later decision, in every run (D5).

        ``engine_version`` follows, so a decision records whether signals were on.
        """
        self._signals_enabled = signals_enabled
        self._provider = provider
        for run in self._runs.values():
            if run.ctx is not None:
                run.ctx.signals_enabled = signals_enabled
                run.ctx.provider = provider

    def track_run(
        self,
        viseca_run_id: str,
        *,
        scenario_id: str | None = None,
        viseca_mandate_id: str | None = None,
        total: int | None = None,
    ) -> RunStatus:
        """Follow a run from its creation, so its progress is read while nothing arrives."""
        run = self._run(viseca_run_id)
        run.scenario_id = scenario_id or run.scenario_id
        run.viseca_mandate_id = viseca_mandate_id or run.viseca_mandate_id
        run.total = max(run.total, total or 0)
        return run.status()

    async def wait_run_recorded(self, viseca_run_id: str) -> RunStatus:
        """Wait until the run's final ``runs`` row (done or error) has committed.

        ``status().runs[].state`` flips in memory before that write; readers of the
        ``runs`` table wait here instead. Raises KeyError for an untracked run.
        """
        run = self._runs[viseca_run_id]
        await run.recorded_final.wait()
        return run.status()

    def run_status(self, viseca_run_id: str) -> RunStatus | None:
        run = self._runs.get(viseca_run_id)
        return run.status() if run else None

    def live_run(self, viseca_run_id: str) -> api.LiveRun | None:
        """D4 ``LiveRun`` for a Viseca run id."""
        run = self._runs.get(viseca_run_id)
        if run is None:
            return None
        status = run.status()
        return api.LiveRun(
            run_id=run.viseca_run_id,
            scenario_id=run.scenario_id or "",
            card_id=run.card_id or "",
            mandate_id=run.mandate_id or run.viseca_mandate_id or "",
            state=run.state,
            delivered=status.delivered,
            decided=status.decided,
            pending_human=status.pending_human,
            total=status.total,
            worker_ok=self.status().ok,
            last_error=run.last_error or self._last_error,
        )

    def status(self) -> WorkerStatus:
        running = self._task is not None and not self._task.done()
        fx_ok = not self.fx_rates_mismatch  # the loop still polls; ok says it is not healthy
        return WorkerStatus(
            state=self._state if running or self._state == "stopped" else "degraded",
            ok=running and self._state == "polling" and self._failures == 0 and fx_ok,
            last_poll_at=self._last_poll_at,
            events_cursor=self._cursor,
            human_window_s=self.human_window_s,
            decision_deadline_s=self.decision_deadline_s,
            pending_step_ups=len(self._expiry),
            history_reseeded=self.history_reseeded,
            fx_rates_match=None if self.fx_rates_mismatch is None else fx_ok,
            fx_rates_mismatch=self.fx_rates_mismatch or [],
            last_error=self._last_error,
            runs=[run.status() for run in self._runs.values()],
        )

    async def start(self) -> None:
        """Read settings and reference data, load history, recover pending step-ups, poll."""
        if self._task is not None:
            return
        self._state = "starting"
        await self._load_settings()
        await self._check_reference_data()
        refresh = self.history_reseeded or any(t.changed for t in self.served_tables)
        if self._history is None or refresh:
            self._history = await asyncio.to_thread(self._load_history)
        if self._ledger is None:
            self._ledger = default_ledger(self._db_engine, self._history)
        await self._recover_pending()
        self._cursor = await asyncio.to_thread(self._load_cursor)
        self._task = asyncio.create_task(self._loop(), name="viseca-worker")

    async def stop(self) -> None:
        """Stop polling and cancel expiry timers (pending step-ups are recovered on start).

        Waits up to ``STOP_DRAIN_S`` for the engine thread's current work, writes every stored
        run's ``runs`` row once more (a request cut short never reached its write), then
        closes any ledger session still open, so no pooled connection outlives the worker.
        """
        tasks = [t for t in [self._task, *self._expiry.values()] if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._expiry.clear()
        self._state = "stopped"
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(self._pool, lambda: None), STOP_DRAIN_S
            )
        except TimeoutError:
            log.warning("the engine thread is still busy after %s s; closing its ledger session", STOP_DRAIN_S)
        # A request cut short above never reached its runs write: bring every stored row up
        # to what the worker knows, so a restarted process (D7) reads the same counts.
        for run in list(self._runs.values()):
            if run.recorded_state is None:
                continue  # never stored (tracked, nothing delivered): nothing to bring up to date
            try:
                await self._persist_run(run)
            except Exception:
                log.exception("could not store run %s on stop", run.run_id)
        close = getattr(self._ledger, "close", None)
        if callable(close):
            close()
        await self.client.drain()

    async def revoke(self, viseca_mandate_id: str) -> None:
        """Revoke a mandate: locally at once (nothing more is approved), then at Viseca."""
        self._revoked.add(viseca_mandate_id)
        if viseca_mandate_id in self._policies:
            self._policies[viseca_mandate_id] = self._policies[viseca_mandate_id].model_copy(
                update={"status": "revoked"}
            )
        for run in self._runs.values():
            if run.viseca_mandate_id == viseca_mandate_id and run.ctx is not None:
                run.ctx.policy = run.ctx.policy.model_copy(update={"status": "revoked"})
        await asyncio.to_thread(self._mark_mandate_revoked, viseca_mandate_id)
        log.info("mandate %s revoked; later requests under it are declined", viseca_mandate_id)
        await self.client.delete_mandate(viseca_mandate_id)

    async def ledger_entries(self, live_ids: list[str]) -> list[LedgerEntry]:
        """The stored entries for these live ids (read on the ledger's thread)."""
        found = await self._engine(lambda: [self.ledger.get(i) for i in live_ids])
        return [e for e in found if e is not None]

    async def resolve_by_customer(
        self, authorization_id: str, decision: Literal["approve", "decline"]
    ) -> LedgerEntry:
        """C8: post the customer's answer to Viseca, then record it (``resolved_by: customer``).

        Raises KeyError (unknown), NotAwaitingAnswer, WindowClosed, or VisecaError (the
        ledger is then unchanged; no second ``/resolve`` is sent for it, and the expiry
        records what the platform shows).
        """
        async with self._resolution_lock:
            entry = await self._engine(self.ledger.get, authorization_id)
            if entry is None:
                raise KeyError(authorization_id)
            if entry.outcome != "step_up" or entry.final:
                raise NotAwaitingAnswer(f"{authorization_id} is not awaiting an answer")
            if entry.deadline_at is not None and self._now() >= entry.deadline_at:
                raise WindowClosed(f"the window for {authorization_id} closed at {entry.deadline_at}")
            if not self._claim_resolve(authorization_id):
                raise NotAwaitingAnswer(f"an answer for {authorization_id} was already sent")
            await self.client.resolve(
                authorization_id,
                decision,
                CUSTOMER_MESSAGES[decision],
                [_resolved_by_row("customer")],
            )
            resolved = await self._engine(
                self.ledger.resolve, authorization_id, decision, "customer", self._now()
            )
            task = self._expiry.pop(authorization_id, None)
            if task is not None:
                task.cancel()
        await self._after_resolution(authorization_id, resolved)
        return resolved

    async def expire(self, authorization_id: str) -> bool:
        """Close an unanswered step-up (rules.md Q2). False if it was already closed.

        The platform expires step-ups itself at the same moment, so its state is read
        first. Already final there → that result is recorded and nothing is posted. Still
        pending (or unreadable) → ``/resolve`` ``decline`` with the timeout message; on a
        409 the state is read again and recorded. Whatever the platform cannot tell us is
        recorded as the timeout decline. At most one ``/resolve`` per live id.
        """
        async with self._resolution_lock:
            entry = await self._engine(self.ledger.get, authorization_id)
            if entry is None or entry.outcome != "step_up" or entry.final:
                return False
            result = await self._platform_result(authorization_id)
            if result is None and self._claim_resolve(authorization_id):
                try:
                    await self.client.resolve(
                        authorization_id,
                        "decline",
                        timeout_message(self.human_window_s),
                        [_resolved_by_row("timeout")],
                    )
                    result = ("decline", "timeout")
                except VisecaError as exc:
                    if exc.status == 409:
                        log.info("Viseca closed step-up %s first (%s)", authorization_id, exc.code)
                        result = await self._platform_result(authorization_id)
                    else:
                        self._note_error(f"timeout resolve of {authorization_id} failed: {exc}")
            # still pending after an answer that was already sent: the platform's own
            # expiry will decline it, so the timeout decline is recorded
            decision, by = result or ("decline", "timeout")
            expired = expired_message(self.human_window_s) if by == "timeout" else None
            resolved = await self._engine(
                partial(self.ledger.resolve, message=expired),
                authorization_id, decision, by, self._now(),
            )
        log.info("step-up %s closed at its window: %s (%s)", authorization_id, resolved.uncertain_outcome, by)
        await self._after_resolution(authorization_id, resolved)
        return True

    def _claim_resolve(self, live_id: str) -> bool:
        """True the first time only: the one ``/resolve`` for ``live_id`` may be sent."""
        if live_id in self._resolve_sent:
            return False
        self._resolve_sent.add(live_id)
        return True

    async def _platform_result(
        self, live_id: str
    ) -> tuple[Literal["approve", "decline"], Literal["customer", "timeout"]] | None:
        """The platform's final answer for a step-up, or None while it waits or is unreadable."""
        run = self._run_of.get(live_id)
        params = {"run_id": run.viseca_run_id} if run is not None and run.viseca_run_id else {}
        try:
            items = await self.client.list_authorizations(**params)
        except VisecaError as exc:
            log.warning("platform state of %s unavailable: %s", live_id, exc)
            return None
        item = next(
            (i for i in items if isinstance(i, dict) and i.get("authorization_id") == live_id), None
        ) if isinstance(items, list) else None  # fmt: skip
        return platform_result(item) if item is not None else None

    # Start-up ----------------------------------------------------------------------------

    async def _load_settings(self) -> None:
        try:
            self.bootstrap = await self.client.bootstrap()
        except VisecaError as exc:
            self._note_error(f"bootstrap failed, using default timeouts: {exc}")
            return
        limits = self.bootstrap.get("limits") if isinstance(self.bootstrap, dict) else None
        limits = limits if isinstance(limits, dict) else {}
        self.human_window_s = (
            positive_number(limits.get("step_up_timeout_seconds"))
            or seconds_setting(self.bootstrap, ("step_up",), ("window", "timeout"), None)
            or seconds_setting(self.bootstrap, ("human",), ("window", "timeout", "seconds"), None)
            or DEFAULT_HUMAN_WINDOW_S
        )
        self.decision_deadline_s = positive_number(
            limits.get("decision_timeout_seconds")
        ) or seconds_setting(self.bootstrap, ("decision",), ("deadline", "timeout"), None)
        long_poll = positive_number(limits.get("long_poll_max_seconds"))
        if long_poll is not None and self._poll_wait_s > long_poll:
            self._poll_wait_s = long_poll
        log.info(
            "Viseca bootstrap: human window %s s, decision deadline %s s, long poll %s s",
            self.human_window_s,
            self.decision_deadline_s,
            self._poll_wait_s,
        )

    async def _check_reference_data(self) -> None:
        try:
            self.reference_data = await self.client.reference_data()
        except VisecaError as exc:
            self._note_error(f"reference data unavailable, keeping the seeded history: {exc}")
            return
        self._check_fx_rates()
        await self._sync_served_tables()
        meta = find_history_metadata(self.reference_data)
        served = str(meta.get("sha256", "")).strip().lower() if meta else ""
        expected = seed_module.pack_file_sha256(HISTORY_FILE, self._data_dir)
        if served and served == expected:
            self.served_history_sha256 = served
            log.info("Viseca history file matches the seeded pack (sha256 %s)", expected)
            return
        try:
            text = await self.client.authorization_history_csv()
        except VisecaError as exc:
            self._note_error(f"history download failed, keeping the seeded history: {exc}")
            return
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if served and digest != served:
            self._note_error(
                f"downloaded history sha256 {digest} does not match the served {served}; not re-seeding"
            )
            return
        self.served_history_sha256 = digest
        if digest == expected:
            log.info("Viseca history file matches the seeded pack (downloaded, sha256 %s)", expected)
            return
        banner = "!" * 72
        log.warning(
            "%s\nVISECA SERVES A DIFFERENT HISTORY FILE: sha256 %s, seeded pack has %s.\n"
            "Re-seeding authorization_history from /v1/reference-data/authorization-history.csv\n%s",
            banner,
            digest,
            expected,
            banner,
        )
        try:
            rows = await asyncio.to_thread(self._reseed_history, text)
        except Exception as exc:
            log.exception("re-seeding history failed")
            self._note_error(f"re-seeding history failed, keeping the seeded history: {exc}")
            return
        self.history_reseeded = True
        log.warning(
            "%s\nRE-SEEDED authorization_history: %d rows from Viseca (sha256 %s)\n%s",
            banner,
            rows,
            digest,
            banner,
        )

    def _check_fx_rates(self) -> None:
        self.fx_rates_mismatch = fx_rate_mismatches(self.reference_data)
        if not self.fx_rates_mismatch:
            log.info("Viseca fx rates match the engine's FX_TO_CHF")
            return
        banner = "!" * 72
        log.error(
            "%s\nVISECA SERVES DIFFERENT FX RATES FROM THE ENGINE'S FX_TO_CHF:\n%s\n"
            "Every CHF amount may be converted wrongly; /healthz stays degraded.\n%s",
            banner,
            "\n".join(self.fx_rates_mismatch),
            banner,
        )
        self._last_error = "fx rates differ from FX_TO_CHF: " + "; ".join(self.fx_rates_mismatch)

    async def _sync_served_tables(self) -> None:
        """Upsert the served reference tables before the history check, so a served history
        file's customers and cards already exist when it is re-seeded."""
        tables = self.reference_data.get("tables") if isinstance(self.reference_data, dict) else None
        if not isinstance(tables, dict):
            self._note_error("reference data serves no tables; keeping the seeded reference tables")
            return
        try:
            self.served_tables = await asyncio.to_thread(self._sync_served, tables)
        except Exception as exc:
            log.exception("syncing the served reference tables failed")
            self._note_error(f"served reference tables not synced, keeping the stored ones: {exc}")
            return
        for t in self.served_tables:
            log.info(
                "reference table %-18s served %3d, store %3d -> %3d rows (%d added, %d updated)%s",
                t.table,
                t.served,
                t.before,
                t.after,
                t.inserted,
                t.updated,
                "" if t.changed else ", unchanged",
            )
        missing = sorted(set(seed_module.SERVED_TABLES) - {t.table for t in self.served_tables})
        ignored = sorted(set(tables) - set(seed_module.SERVED_TABLES))
        if missing:
            log.info("reference tables not served, kept as stored: %s", ", ".join(missing))
        if ignored:
            log.info("served tables the store does not keep, ignored: %s", ", ".join(ignored))

    def _sync_served(self, tables: dict[str, Any]) -> list[seed_module.TableSync]:
        with session(self._db_engine) as s:
            return seed_module.sync_served(s, tables)

    def _reseed_history(self, text: str) -> int:
        with session(self._db_engine) as s:
            return seed_module.reseed_history(s, text)

    def _load_history(self) -> StoreHistoryIndex:
        with session(self._db_engine) as s:
            return StoreHistoryIndex.load(s)

    async def _recover_pending(self) -> None:
        """Re-arm the expiry of every pending step-up from a live run.

        Only ``runs.kind = live``: a replay step-up was never posted to Viseca, so a timeout
        ``/resolve`` for it would only be refused (404); a run with no row is a replay, as
        in the ledger.
        """
        rows = await asyncio.to_thread(self._load_events)
        for live_id, source_id, event, kind in rows:
            self._events[live_id] = event
            self.source_ids[live_id] = source_id
            related = event.get("authorization", {}).get("related_authorization_id")
            if related:
                self.related_ids[live_id] = related
            if kind != "live":
                continue
            entry = await self._engine(self.ledger.get, live_id)
            if entry is not None and entry.outcome == "step_up" and not entry.final:
                deadline = entry.deadline_at or self._now()
                log.info("recovered pending step-up %s, closes at %s", live_id, deadline)
                self._schedule_expiry(live_id, deadline)

    def _load_events(self) -> list[tuple[str, str, dict[str, Any], str | None]]:
        with session(self._db_engine) as s:
            rows = s.execute(
                select(
                    EventRaw.live_authorization_id,
                    EventRaw.source_authorization_id,
                    EventRaw.event,
                    Run.kind,
                ).outerjoin(Run, Run.run_id == EventRaw.run_id)
            ).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    # The loop ----------------------------------------------------------------------------

    async def _loop(self) -> None:
        self._state = "polling"
        while True:
            try:
                envelope = await self.client.next_decision_request(wait=self._poll_wait_s)
                self._last_poll_at = self._now()
                if envelope is None:
                    await self._idle()
                elif not isinstance(envelope, dict):
                    raise VisecaError(200, "invalid_response", "decision request is not an object")
                else:
                    await self._handle(envelope)
                    self._after_handled(envelope)
                self._failures = 0
                self._state = "polling"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures += 1
                self._state = "degraded"
                if isinstance(exc, VisecaError):
                    self._note_error(f"poll failed: {exc}")
                else:
                    log.exception("worker loop error")
                    self._note_error(f"worker error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(min(LOOP_BACKOFF_MAX_S, 0.25 * 2 ** min(self._failures, 6)))

    async def _idle(self) -> None:
        for run in list(self._runs.values()):
            if run.state in ("done", "error"):
                continue
            try:
                progress = await self.client.get_run(run.viseca_run_id)
            except VisecaError as exc:
                log.warning("progress of run %s unavailable: %s", run.viseca_run_id, exc)
                continue
            self._apply_progress(run, progress)
            await self._persist_run(run)
        await self._sync_events()

    def _apply_progress(self, run: RunState, progress: Any) -> None:
        total = run_total(progress)
        if total is not None:
            run.total = max(run.total, total)
        state = progress.get("status") if isinstance(progress, dict) else None
        state = str(state or first_value(progress, "status", "state") or "").lower()
        if state in _DONE_STATES:
            run.platform_done = True
        elif state in _ERROR_STATES:
            run.state = "error"
            run.last_error = f"Viseca reports the run as {state}"
            run.finished_at = run.finished_at or self._now()
        self._maybe_done(run)

    def _maybe_done(self, run: RunState) -> None:
        if run.state in ("done", "error"):
            return
        if run.platform_done and not run.pending:
            run.state = "done"
            run.finished_at = self._now()

    async def _handle(self, envelope: dict[str, Any]) -> None:
        data = envelope.get("data")
        errors = event_errors(data)
        if errors:
            await self._reject_invalid(envelope, errors)
            return
        assert isinstance(data, dict)
        received_at = self._now()
        auth = data["authorization"]
        live_id: str = auth["authorization_id"]
        deadline_at = _parse_time(data["deadline_at"]) or received_at
        viseca_run_id = str(envelope.get("run_id") or "")
        bound = viseca_run_id in self._runs and self._runs[viseca_run_id].ctx is not None
        run = self._bind_run(viseca_run_id, data)
        if not bound:
            # The ledger reads runs.kind (live) to carry the session watch and remembered
            # answers over from earlier live runs, so the row is written before the first
            # decision; a run with no row is treated as a replay.
            await self._persist_run(run)
        self._run_of[live_id] = run
        self._events[live_id] = data
        self.source_ids[live_id] = auth["source_authorization_id"]
        if auth["related_authorization_id"]:
            self.related_ids[live_id] = auth["related_authorization_id"]

        stored = await self._engine(self.ledger.get, live_id)
        if stored is not None and stored.outcome == "step_up" and envelope.get("status") == STEP_UP_WAITING:
            await self._step_up_still_waiting(stored)
            return
        if stored is not None:
            run.redeliveries += 1
            log.info("redelivery of %s: posting the stored %s, counting nothing", live_id, stored.outcome)
            await self._post_stored(data, stored, deadline_at)
            return

        if live_id not in run.live_ids:
            run.live_ids.append(live_id)
        save = asyncio.create_task(
            asyncio.to_thread(self._save_event, run.run_id, data, received_at, deadline_at)
        )
        extra = await self._reconcile_context(run, data)
        extra.extend(self._feed_mismatches)
        self._feed_mismatches.clear()

        decision = await self._decide(run, data, extra, deadline_at)
        if decision is not None:
            await self._post_new(run, data, decision, deadline_at)
        try:
            await save
        except Exception as exc:
            log.exception("events_raw write failed")
            self._note_error(f"events_raw write for {live_id} failed: {exc}")
        await self._sync_events()
        await self._persist_run(run)

    def _run(self, viseca_run_id: str) -> RunState:
        run = self._runs.get(viseca_run_id)
        if run is None:
            run = RunState(
                viseca_run_id=viseca_run_id, run_id=f"live-{viseca_run_id}", started_at=self._now()
            )
            self._runs[viseca_run_id] = run
        return run

    def _bind_run(self, viseca_run_id: str, data: dict[str, Any]) -> RunState:
        run = self._run(viseca_run_id)
        if run.ctx is not None:
            return run
        auth, mandate = data["authorization"], data["mandate"]
        tm = mandate["mandate_id"]
        policy = self._policies.get(tm)
        if policy is None:
            log.warning(
                "no confirmed policy bound for mandate %s; deciding from the platform snapshot", tm
            )
            policy = policy_from_snapshot(mandate)
        if tm in self._revoked and policy.status == "active":
            policy = policy.model_copy(update={"status": "revoked"})
        run.viseca_mandate_id = tm
        run.mandate_id = policy.mandate_id
        run.card_id = auth["card_id"]
        run.scenario_id = run.scenario_id or auth.get("scenario_id")
        run.state = "running"
        run.ctx = PipelineContext(
            policy=policy,
            ledger=cast(Ledger, ClaimedLedger(self.ledger, self._claims)),
            history=self._history or StoreHistoryIndex(),
            run_id=run.run_id,
            provider=self._provider,
            signals_enabled=self._signals_enabled,
            budget_ms=self._budget_ms,
            human_window_s=math.ceil(self.human_window_s),
            implementations=self._implementations,
            stubbed=self._stubbed,
            now=self._now,
        )
        return run

    # Deciding ----------------------------------------------------------------------------

    async def _engine(self, fn: Callable[..., T], *args: Any) -> T:
        """Run ``fn`` on the engine thread inside one short ledger session."""
        return await asyncio.get_running_loop().run_in_executor(
            self._pool, partial(self._in_scope, fn, *args)
        )

    def _in_scope(self, fn: Callable[..., T], *args: Any) -> T:
        scope = getattr(self._ledger, "scope", None)
        if scope is None:
            return fn(*args)
        with scope():
            return fn(*args)

    async def _reconcile_context(self, run: RunState, data: dict[str, Any]) -> list[EvidenceRow]:
        theirs = data["context"]["approved_spend_in_period_chf"]
        if theirs is None or run.ctx is None:
            return []
        at = _parse_time(data["authorization"]["timestamp"])
        assert at is not None
        ours = await self._engine(
            self._approved_spend, list(run.live_ids), at, period_days_of(run.ctx.policy, spend_only=True)
        )
        if abs(Decimal(str(theirs)) - ours) <= RECONCILE_TOLERANCE_CHF:
            return []
        log.warning("context mismatch on %s: Viseca CHF %s, ledger CHF %s", run.viseca_run_id, theirs, ours)
        return [
            EvidenceRow(
                rule="ledger_mismatch",
                outcome="info",
                detail=f"Viseca counts CHF {float(theirs):.2f} approved in this period; "
                f"our ledger counts CHF {ours:.2f}.",
                source="ledger",
            )
        ]

    def _approved_spend(self, live_ids: list[str], at: datetime, period_days: int | None) -> Decimal:
        start = at - timedelta(days=period_days) if period_days else None
        total = Decimal(0)
        for live_id in live_ids:
            entry = self.ledger.get(live_id)
            if entry is None or not entry.spent_chf or entry.ts_sim >= at:
                continue
            if start is None or entry.ts_sim >= start:
                total += Decimal(str(entry.spent_chf))
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    async def _decide(
        self,
        run: RunState,
        data: dict[str, Any],
        extra: list[EvidenceRow],
        deadline_at: datetime,
    ) -> api.Decision | None:
        """The pipeline's decision, or None after posting a decision in its place.

        An engine error posts a decline; an engine still running ``POST_MARGIN_S`` before
        ``deadline_at`` gets the overrun decision its uncertainty setting names
        (``_overrun_entry``, ``_post_overrun``).
        """
        assert run.ctx is not None
        live_id = data["authorization"]["authorization_id"]
        future = asyncio.get_running_loop().run_in_executor(
            self._pool, partial(self._in_scope, decide_event, data, run.ctx, extra)
        )
        waits = [self._budget_ms / 1000]
        waits.append((deadline_at - self._now()).total_seconds() - POST_MARGIN_S - waits[0])
        try:
            for i, wait in enumerate(waits):
                if wait <= 0:
                    continue
                try:
                    _, _, decision = await asyncio.wait_for(asyncio.shield(future), wait)
                    return decision
                except TimeoutError:
                    if i == 0:
                        log.warning("engine over its %d ms budget on %s", self._budget_ms, live_id)
                except Exception as exc:
                    log.exception("engine failed on %s", live_id)
                    await self._decline_unevaluable(
                        run, data, deadline_at, f"The engine failed: {type(exc).__name__}."
                    )
                    return None
            entry = self._overrun_entry(run, data)
            if not self._claims.claim(entry):
                # The engine recorded its decision a moment ago; only its reply is left.
                try:
                    _, _, decision = await asyncio.shield(future)
                    return decision
                except Exception:
                    log.exception("engine failed on %s after recording", live_id)
                    stored = await self._engine(self.ledger.get, live_id)
                    if stored is not None:
                        await self._post_stored(data, stored, deadline_at)
                    return None
            future.add_done_callback(partial(_late_result, live_id))
            await self._post_overrun(run, data, entry, deadline_at)
            return None
        finally:
            self._claims.forget(live_id)

    def _overrun_entry(self, run: RunState, data: dict[str, Any]) -> LedgerEntry:
        """The decision that stands in for an engine over its budget (D2, D3).

        It follows the uncertainty setting of the policy the worker decides with: ``decline``
        posts a final decline; ``ask`` and ``approve`` post a pending step-up, because
        approve is never automatic.
        """
        assert run.ctx is not None
        auth = data["authorization"]
        detail = f"The engine did not finish within {self._budget_ms} ms."
        decided_at = self._now()
        decline = overrun_setting(run.ctx.policy) == "decline"
        return LedgerEntry(
            live_authorization_id=auth["authorization_id"],
            run_id=run.run_id,
            mandate_id=run.ctx.policy.mandate_id,
            card_id=auth["card_id"],
            customer_id=data["mandate"]["customer_id"],
            ts_sim=datetime.fromisoformat(auth["timestamp"]),
            outcome="decline" if decline else "step_up",
            final=decline,
            uncertain_outcome=None if decline else "pending",
            merchant_id=auth["merchant"]["merchant_id"],
            item_ids=[line["item_id"] for line in auth["items"]],
            billing_amount_chf=auth["billing_amount_chf"],
            step=4,
            deciding_ids=["engine"],
            reason_codes=["unevaluable"],
            evidence=[EvidenceRow(rule="engine", outcome="uncertain", detail=detail, source="policy")],
            message=OVERRUN_DECLINE_MESSAGE if decline else OVERRUN_MESSAGE,
            engine_version=run.ctx.engine_version,
            latency_ms=float(self._budget_ms),
            signals_enabled=run.ctx.signals_enabled,
            decided_at=decided_at,
            deadline_at=None if decline else decided_at + timedelta(seconds=run.ctx.human_window_s),
        )

    async def _post_overrun(
        self, run: RunState, data: dict[str, Any], entry: LedgerEntry, deadline_at: datetime
    ) -> None:
        """POST the claimed overrun decision, then record it (and, for a step-up, wait).

        The POST goes out at once. The ledger thread is still busy with the engine, so the
        entry is stored there once the engine returns: by the engine's own ``record`` or by
        ``_settle_overrun``. A decline is final and counts nothing. A step-up (with its
        reservation) is from then on a pending step-up like any other: C8, expiry at
        accepted time + human window, redelivery.
        """
        assert run.ctx is not None
        live_id = entry.live_authorization_id
        run.last_error = f"{live_id}: {entry.evidence[0].detail}"
        try:
            reply = await self._post(
                live_id,
                entry.outcome,
                entry.reason_codes,
                entry.message,
                [row.model_dump(mode="json") for row in entry.evidence],
                entry.engine_version,
                deadline_at,
            )
        except VisecaError as exc:
            reply = None
            self._note_error(f"overrun {entry.outcome} of {live_id} not accepted: {exc}")
        run.decided.add(live_id)
        stored, view = await self._engine(
            self._settle_overrun, live_id, period_days_of(run.ctx.policy)
        )
        decision = to_api_decision(data, stored, view, run.ctx.policy)
        if stored.outcome == "step_up" and not stored.final:
            await self._await_answer(run, decision, reply)
        else:
            self._notify(decision)

    def _settle_overrun(self, live_id: str, period_days: int | None) -> tuple[LedgerEntry, LedgerView]:
        claimed = self._claims.take(live_id)
        stored = self.ledger.record(claimed) if claimed is not None else self.ledger.get(live_id)
        assert stored is not None
        view = self.ledger.view(
            run_id=stored.run_id,
            customer_id=stored.customer_id,
            card_id=stored.card_id,
            at=stored.ts_sim,
            period_days=period_days,
        )
        return stored, view

    async def _decline_unevaluable(
        self, run: RunState, data: dict[str, Any], deadline_at: datetime, detail: str
    ) -> None:
        """Decline what the engine failed on (missing is never a pass)."""
        assert run.ctx is not None
        auth = data["authorization"]
        live_id = auth["authorization_id"]
        evidence = [EvidenceRow(rule="engine", outcome="uncertain", detail=detail, source="policy")]
        entry = LedgerEntry(
            live_authorization_id=live_id,
            run_id=run.run_id,
            mandate_id=run.ctx.policy.mandate_id,
            card_id=auth["card_id"],
            customer_id=data["mandate"]["customer_id"],
            ts_sim=datetime.fromisoformat(auth["timestamp"]),
            outcome="decline",
            final=True,
            uncertain_outcome=None,
            merchant_id=auth["merchant"]["merchant_id"],
            item_ids=[line["item_id"] for line in auth["items"]],
            billing_amount_chf=auth["billing_amount_chf"],
            step=4,
            deciding_ids=["engine"],
            reason_codes=["unevaluable"],
            evidence=evidence,
            message=FALLBACK_MESSAGE,
            engine_version=run.ctx.engine_version,
            latency_ms=0.0,
            signals_enabled=run.ctx.signals_enabled,
            decided_at=self._now(),
        )
        try:
            await self._engine(self.ledger.record, entry)
        except Exception as exc:
            log.exception("fallback record failed")
            self._note_error(f"could not record the fallback decline of {live_id}: {exc}")
        run.last_error = f"{live_id}: {detail}"
        try:
            await self._post(
                live_id,
                "decline",
                ["unevaluable"],
                FALLBACK_MESSAGE,
                [row.model_dump(mode="json") for row in evidence],
                run.ctx.engine_version,
                deadline_at,
            )
        except VisecaError as exc:
            self._note_error(f"fallback decline of {live_id} not accepted: {exc}")
        run.decided.add(live_id)

    async def _reject_invalid(self, envelope: dict[str, Any], errors: list[str]) -> None:
        data = envelope.get("data")
        auth = data.get("authorization") if isinstance(data, dict) else None
        live_id = auth.get("authorization_id") if isinstance(auth, dict) else None
        live_id = live_id if isinstance(live_id, str) and live_id else envelope.get("authorization_id")
        self._note_error(f"request {live_id or '?'} failed schema validation: {'; '.join(errors[:3])}")
        if not isinstance(live_id, str) or not live_id:
            return
        deadline = (
            _parse_time(data.get("deadline_at")) if isinstance(data, dict) else None
        ) or self._now() + timedelta(seconds=5)
        evidence = [
            {
                "rule": "event_schema",
                "outcome": "fail",
                "detail": cap("; ".join(errors[:3]), 500),
                "source": "policy",
            }
        ]
        try:
            await self._post(
                live_id, "decline", ["unevaluable"], INVALID_EVENT_MESSAGE, evidence,
                f"oneguard/{__version__}", deadline,
            )  # fmt: skip
        except VisecaError as exc:
            self._note_error(f"decline of invalid request {live_id} not accepted: {exc}")

    # Posting -----------------------------------------------------------------------------

    async def _post(
        self,
        live_id: str,
        outcome: str,
        reason_codes: list[str],
        message: str,
        evidence: list[dict[str, Any]],
        engine_version: str,
        deadline_at: datetime,
    ) -> dict[str, Any] | None:
        """POST a decision, retrying transient failures until the deadline.

        Returns the platform's response, or None when it already holds a decision (409).
        A 400/422 on the full body is retried once without ``evidence`` and
        ``engine_version`` so a decision still lands in time.
        """
        extras: dict[str, Any] = {"evidence": evidence, "engine_version": engine_version}
        attempt = 0
        while True:
            try:
                reply = await self.client.post_decision(
                    live_id, outcome, reason_codes=reason_codes, customer_message=message, **extras
                )
                return reply or {}
            except VisecaError as exc:
                if exc.status == 409:
                    log.info("Viseca already holds a decision for %s (%s)", live_id, exc.code)
                    return None
                if exc.status in (400, 422) and extras:
                    log.warning("Viseca rejected the full decision body for %s (%s); retrying minimal", live_id, exc.code)
                    extras = {}
                    continue
                left = (deadline_at - self._now()).total_seconds()
                transient = exc.status is None or exc.status == 429 or exc.status >= 500
                if not transient or left <= POST_RETRY_DELAYS_S[0]:
                    raise
                delay = POST_RETRY_DELAYS_S[min(attempt, len(POST_RETRY_DELAYS_S) - 1)]
                await asyncio.sleep(min(delay, left - POST_RETRY_DELAYS_S[0]))
                attempt += 1

    async def _post_new(
        self, run: RunState, data: dict[str, Any], decision: api.Decision, deadline_at: datetime
    ) -> None:
        live_id = decision.authorization_id
        evidence = [row.model_dump(mode="json") for row in decision.evidence]
        outcome = {"approved": "approve", "stopped": "decline", "uncertain": "step_up"}[decision.decision]
        try:
            reply = await self._post(
                live_id,
                outcome,
                decision.reason_codes,
                decision.message,
                evidence,
                decision.engine_version or f"oneguard/{__version__}",
                deadline_at,
            )
        except VisecaError as exc:
            reply = None
            run.last_error = f"{live_id}: decision not accepted: {exc}"
            self._note_error(run.last_error)
        run.decided.add(live_id)
        if outcome == "step_up" and decision.status == "pending_human":
            await self._await_answer(run, decision, reply)
        else:
            self._notify(decision)

    async def _await_answer(
        self, run: RunState, decision: api.Decision, reply: dict[str, Any] | None
    ) -> None:
        """A posted pending step-up: window from Viseca's accepted time, then expiry (Q2)."""
        live_id = decision.authorization_id
        run.pending.add(live_id)
        deadline = decision.deadline_at
        if reply is not None:
            expires = _parse_time(reply.get("step_up_expires_at"))
            if expires is None:
                accepted = next(
                    (t for t in (_parse_time(first_value(reply, k)) for k in _ACCEPTED_KEYS) if t), None
                ) or self._now()
                expires = accepted + timedelta(seconds=self.human_window_s)
            deadline = await self._set_deadline(live_id, expires, deadline)
            decision = decision.model_copy(update={"deadline_at": deadline})
        self._schedule_expiry(live_id, deadline or self._now())
        self._notify(decision)

    async def _set_deadline(
        self, live_id: str, deadline: datetime, fallback: datetime | None
    ) -> datetime | None:
        try:
            entry = await self._engine(self.ledger.set_deadline, live_id, deadline)
        except NotImplementedError:
            if not self._warned_no_set_deadline:
                log.warning("the ledger cannot move deadlines; step-ups keep the local default")
                self._warned_no_set_deadline = True
            return fallback
        return entry.deadline_at

    async def _post_stored(self, data: dict[str, Any], stored: LedgerEntry, deadline_at: datetime) -> None:
        """Answer a redelivery with the decision already made; nothing is counted again."""
        live_id = stored.live_authorization_id
        try:
            await self._post(
                live_id,
                stored.outcome,
                stored.reason_codes,
                stored.message,
                [row.model_dump(mode="json") for row in stored.evidence],
                stored.engine_version,
                deadline_at,
            )
        except VisecaError as exc:
            self._note_error(f"re-post of {live_id} not accepted: {exc}")
            return
        if stored.outcome == "step_up" and not stored.final and live_id not in self._expiry:
            self._schedule_expiry(live_id, stored.deadline_at or self._now())

    async def _step_up_still_waiting(self, stored: LedgerEntry) -> None:
        """The platform served a step-up we posted that still waits for ``/resolve``.

        Nothing is posted or counted. Pending with us: make sure its expiry is scheduled.
        Already closed with us, the envelope is older than our answer (its ``/resolve`` is
        sent or on its way), so nothing is resent. Then pause, because the platform serves
        a waiting step-up on every poll without waiting.
        """
        live_id = stored.live_authorization_id
        if not stored.final and live_id not in self._expiry:
            self._schedule_expiry(live_id, stored.deadline_at or self._now())
        await asyncio.sleep(self._waiting_step_up_pause_s)

    # Step-up expiry ----------------------------------------------------------------------

    def _schedule_expiry(self, live_id: str, deadline: datetime) -> None:
        old = self._expiry.pop(live_id, None)
        if old is not None:
            old.cancel()
        self._expiry[live_id] = asyncio.create_task(
            self._expire_at(live_id, deadline), name=f"expire-{live_id}"
        )

    async def _expire_at(self, live_id: str, deadline: datetime) -> None:
        try:
            await asyncio.sleep(max(0.0, (deadline - self._now()).total_seconds()))
            await self.expire(live_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("expiry of %s failed", live_id)
            self._note_error(f"expiry of {live_id} failed: {exc}")
        finally:
            if self._expiry.get(live_id) is asyncio.current_task():
                del self._expiry[live_id]

    async def _after_resolution(self, live_id: str, entry: LedgerEntry) -> None:
        run = self._run_of.get(live_id)
        if run is not None:
            run.pending.discard(live_id)
            self._maybe_done(run)
            await self._persist_run(run)
        event = self._events.get(live_id)
        if event is None or run is None or run.ctx is None:
            return
        view = await self._engine(
            partial(
                self.ledger.view,
                run_id=entry.run_id,
                customer_id=entry.customer_id,
                card_id=entry.card_id,
                at=entry.ts_sim,
                period_days=period_days_of(run.ctx.policy),
            )
        )
        self._notify(to_api_decision(event, entry, view, run.ctx.policy))

    # Event feed --------------------------------------------------------------------------

    async def _sync_events(self) -> None:
        try:
            reply = await self.client.events(since=self._cursor)
        except VisecaError as exc:
            log.warning("event feed unavailable: %s", exc)
            return
        items = reply.get("events") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            log.warning("event feed reply has no events list; cursor stays at %s", self._cursor)
            return
        next_cursor = reply.get("next_cursor")
        async with self._resolution_lock:
            for item in items:
                if isinstance(item, dict):
                    await self._check_feed_item(item)
        if next_cursor is not None and next_cursor != self._cursor:
            await asyncio.to_thread(self._save_cursor, next_cursor)
            self._cursor = next_cursor

    async def _check_feed_item(self, item: dict[str, Any]) -> None:
        live_id = item.get("authorization_id")
        status = item.get("status")
        platform = _FEED_FINAL.get(str(status).lower()) if status else None
        if not isinstance(live_id, str) or platform is None:
            return
        entry = await self._engine(self.ledger.get, live_id)
        if entry is None:
            return
        ours = _ledger_status(entry)
        if ours == platform or (live_id, platform) in self._feed_seen:
            return
        self._feed_seen.add((live_id, platform))
        log.warning("event feed mismatch on %s: Viseca %s, ledger %s", live_id, platform, ours)
        self._feed_mismatches.append(
            EvidenceRow(
                rule="ledger_mismatch",
                outcome="info",
                detail=f"Viseca's event feed shows {live_id} as {platform}; our ledger has it as {ours}.",
                source="ledger",
            )
        )

    # Store -------------------------------------------------------------------------------

    def _save_event(
        self, run_id: str, data: dict[str, Any], received_at: datetime, deadline_at: datetime
    ) -> None:
        auth = data["authorization"]
        with session(self._db_engine) as s:
            if s.get(EventRaw, auth["authorization_id"]) is not None:
                return
            s.add(
                EventRaw(
                    live_authorization_id=auth["authorization_id"],
                    run_id=run_id,
                    source_authorization_id=auth["source_authorization_id"],
                    received_at=received_at,
                    deadline_at=deadline_at,
                    event=data,
                )
            )

    async def _persist_run(self, run: RunState) -> None:
        await asyncio.to_thread(self._save_run, run)
        if run.recorded_state in ("done", "error"):
            run.recorded_final.set()

    def _save_run(self, run: RunState) -> None:
        with self._run_write_lock:
            status = run.status()
            with session(self._db_engine) as s:
                s.merge(
                    Run(
                        run_id=run.run_id,
                        viseca_run_id=run.viseca_run_id,
                        kind="live",
                        scenario_id=run.scenario_id,
                        mandate_id=run.mandate_id or run.viseca_mandate_id or "",
                        card_id=run.card_id or "",
                        state=status.state,
                        delivered=status.delivered,
                        decided=status.decided,
                        pending_human=status.pending_human,
                        total=status.total,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        worker_last_poll_at=self._last_poll_at,
                        last_error=run.last_error,
                    )
                )
            run.recorded_state = status.state  # committed: the session block has exited

    def _load_cursor(self) -> int | str:
        with session(self._db_engine) as s:
            row = s.get(WorkerState, EVENTS_CURSOR_KEY)
        if row is None or not isinstance(row.value, int | str) or isinstance(row.value, bool):
            log.info("no stored event feed cursor; reading the feed from 0")
            return 0
        log.info("event feed cursor resumed at %s", row.value)
        return row.value

    def _save_cursor(self, cursor: int | str) -> None:
        with session(self._db_engine) as s:
            s.merge(WorkerState(key=EVENTS_CURSOR_KEY, value=cursor, updated_at=self._now()))

    def _mark_mandate_revoked(self, viseca_mandate_id: str) -> None:
        with session(self._db_engine) as s:
            for mandate in s.scalars(select(Mandate).where(Mandate.viseca_mandate_id == viseca_mandate_id)):
                if mandate.status != "revoked":
                    mandate.status = "revoked"
                    mandate.revoked_at = self._now()

    # Helpers -----------------------------------------------------------------------------

    def _after_handled(self, envelope: dict[str, Any]) -> None:
        live_id = str(envelope.get("authorization_id") or "")
        for listener in self._handled_listeners:
            try:
                listener(live_id)
            except Exception:
                log.exception("handled listener failed")

    def _notify(self, decision: api.Decision) -> None:
        for listener in self._listeners:
            try:
                listener(decision)
            except Exception:
                log.exception("decision listener failed")

    def _note_error(self, message: str) -> None:
        self._last_error = message
        log.error("%s", message)


def overrun_setting(policy: Policy) -> Literal["ask", "decline", "approve"]:
    """The uncertainty setting an overrun follows; missing or unknown reads as ``ask``."""
    setting = getattr(policy, "uncertainty_policy", None)
    if setting in ("ask", "decline", "approve"):
        return setting
    log.warning(
        "mandate %s has uncertainty setting %r; an overrun is treated as ask", policy.mandate_id, setting
    )
    return "ask"


def _late_result(live_id: str, future: Future[Any] | asyncio.Future[Any]) -> None:
    """The engine returned after the overrun decision was posted; that decision stands.

    The engine's ``record`` stored the claimed overrun decision, not its own outcome, so
    nothing is posted or counted again.
    """
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        log.warning("engine failed on %s after the overrun decision: %r", live_id, exc)
        return
    log.info("engine finished %s after the overrun decision; that decision stands", live_id)


def run_total(progress: Any) -> int | None:
    """The run's event count: ``generated_event_count`` on the live sandbox."""
    for key in _TOTAL_KEYS:
        value = progress.get(key) if isinstance(progress, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    value = first_value(progress, *_TOTAL_KEYS)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _resolved_by_row(by: Literal["customer", "timeout"]) -> dict[str, Any]:
    return {"rule": "resolved_by", "outcome": "info", "detail": by, "source": "ledger"}


def _ledger_status(entry: LedgerEntry) -> str:
    if entry.outcome == "approve":
        return "approved"
    if entry.outcome == "decline":
        return "declined"
    if not entry.final:
        return "pending"
    return "approved" if entry.uncertain_outcome == "approved" else "declined"
