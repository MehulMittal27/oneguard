"""The long-poll worker between the Viseca sandbox and the pipeline.

Runs as an asyncio task inside the API process (docs/architecture.md Runtime). One
loop, never blocked by a human:

- ``start``: ``GET /v1/bootstrap`` (``limits``: human window, decision deadline, long-poll
  cap; ``pack_version``), then, in the lease holder only, the reference sync:
  ``GET /v1/reference-data``. Every reference table it serves under ``tables``
  (customers, accounts, cards, merchants, items, fx rates, the scenario catalogue) is
  upserted into the store in one transaction when it differs from the stored rows, with
  per-table counts logged (``seed.sync_served``); served-only customers, cards and
  scenarios then exist for the API. The sandbox serves no history-file hash, so the
  file is downloaded from ``/v1/reference-data/authorization-history.csv`` and hashed; if
  it differs from the one the seed checked (``data/metadata.json``),
  ``authorization_history`` is re-seeded from it and that is logged loudly. The served
  ``tables.fx_rates`` must equal ``facts.FX_TO_CHF`` (the rates every CHF amount is
  converted with); a mismatch is logged loudly and keeps ``ok`` false (``/healthz`` degraded). The served
  scenario ids go to ``worker_state`` (``served_scenarios``, C12 ``live``).
- the reference sync runs again while the worker polls (``_sync_reference``; lease holder
  only, concurrent triggers join the sync in flight, a failure is logged and never stops a
  decision): when a bootstrap re-read shows a new ``pack_version``; when
  ``/v1/reference-data`` (read every ``REFERENCE_CHECK_S``) shows another pack version or
  other row counts than the last sync; and when an event names a merchant, item, customer
  or card the store does not know. That event waits at most ``UNKNOWN_ID_SYNC_S`` for it,
  then is decided with what the store has: no catalogue price range, the merchant's
  category and country from the event, no history (never familiar), plus an ``info``
  evidence row naming the ids. After a change the history index is reloaded in place
  (``ReloadableHistory``), so the ledger, every run and the API read the new one.
- every run start (D3 before it creates the run, and the first sight of a run: ``track_run``
  or its first event) re-reads ``/v1/bootstrap`` unless it was read in the last
  ``BOOTSTRAP_FRESH_S``: a changed human window, decision deadline or long-poll wait is
  logged and used from then on, in every run.
- which customer and card a served scenario runs on: the catalogue does not say, the
  platform does in the bootstrap ``profile``, every run's ``fixture_profiles`` and every
  authorization. Each sighting is upserted into ``scenario_profiles`` (``remember_profiles``):
  at start, from D3's run reply, from run progress, from a run's first event and the feed.
- start also reads ``GET /v1/authorizations`` once: its runs name their cards, and a step-up
  the platform still serves as waiting that the ledger already closed by the timeout rule
  gets its timeout ``/resolve`` now, so the platform stops serving it.
- loop: long-poll ``/v1/decision-requests/next?wait=25``. 204 → read the progress of
  every tracked run and the event feed, poll again. 200 → validate ``data`` against the
  event schema (properties it does not list are logged and passed on; a missing required
  field or a wrong type declines with an ``event_schema`` evidence row), remember the
  live → source id map (and the live related id), store the full event in ``events_raw``,
  reconcile ``context.approved_spend_in_period_chf`` against the ledger, run ``pipeline.decide_event`` within ``ONEGUARD_ENGINE_BUDGET_MS``
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

- one worker polls per store (``store/lease.py``): ``start`` takes the worker lease (a
  Postgres advisory lock; always granted on SQLite). Without it the worker stays in
  ``standby``: no poll, no step-up recovery, no feed; it retries and takes over once the
  holder stops, and a holder whose lease is gone stands by again.
- a run's row: counters recomputed from ``events_raw`` and the ledger on every write,
  ``started_at`` kept from the first. A finished run is closed from ``GET
  /v1/scenario-runs/{id}`` on a 204, on the feed's ``scenario.completed`` (the feed is read
  at least every ``FEED_SYNC_S``) and at start for live rows still ``running``.

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
import json
import logging
import math
import threading
import time
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
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
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
from oneguard.store.history import ReloadableHistory, StoreHistoryIndex
from oneguard.store.lease import WorkerLease, worker_lease
from oneguard.store.schema import (
    Account,
    Card,
    Customer,
    Decision,
    EventRaw,
    Item,
    Mandate,
    Merchant,
    Run,
    ScenarioProfile,
    WorkerState,
)
from oneguard.viseca.client import VisecaClient, VisecaError, cap
from oneguard.viseca.schema import check_event

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
STANDBY_RETRY_S = 5.0
"""How often a worker in standby tries to take the lease."""
LEASE_CHECK_S = 10.0
"""How often the polling worker checks that its lease still holds."""
FEED_SYNC_S = 5.0
"""The event feed is read at least this often, also while waiting step-ups keep every poll
busy (no 204): its ``scenario.completed`` closes finished runs."""
REFERENCE_CHECK_S = 300.0
"""How often the polling worker compares ``/v1/reference-data`` (pack version, row counts)
with the last reference sync."""
UNKNOWN_ID_SYNC_S = 1.0
"""Longest a decision waits for the reference sync an unknown id started (the platform's
deadline is 8 s); then it is decided with what the store has."""
BOOTSTRAP_FRESH_S = 30.0
"""A run start re-reads ``/v1/bootstrap`` unless it was read this recently (D3 reads it just
before it creates the run)."""
RECONCILE_TOLERANCE_CHF = Decimal("0.005")
HISTORY_FILE = "authorization_history.csv"
EVENTS_CURSOR_KEY = "events_cursor"
SERVED_SCENARIOS_KEY = "served_scenarios"
"""``worker_state`` row: the scenario ids the platform served at the last start (C12 ``live``)."""

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
PLATFORM_PENDING = frozenset({"awaiting_decision", STEP_UP_WAITING})
"""``GET /v1/authorizations`` statuses of a purchase still open at the platform."""


def run_finished(progress: Any) -> bool:
    """True when a ``GET /v1/scenario-runs/{id}`` reply says the run is over (done or failed)."""
    state = progress.get("status") if isinstance(progress, dict) else None
    state = str(state or first_value(progress, "status", "state") or "").lower()
    return state in _DONE_STATES or state in _ERROR_STATES
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
    """A fixture profile the platform names: the scenario, its customer and card.

    ``customer_id`` is None when the platform named only the card (an authorization);
    the store then finds the customer through the card.
    """

    scenario_id: str
    customer_id: str | None
    card_id: str
    profile_id: str | None = None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


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
    profile_id = profile.get("profile_id") or context.get("profile_id")
    return ServedProfile(
        scenario_id=scenario, customer_id=customer, card_id=card, profile_id=profile_id if _text(profile_id) else None
    )


def profile_sightings(obj: Any) -> list[ServedProfile]:
    """Every scenario → customer / card binding the platform states anywhere in ``obj``.

    Three shapes: the bootstrap ``profile`` (``scenario_id`` + ``profile_context``), a run
    (reply, progress, feed item: ``scenario_id`` + ``fixture_profiles[{profile_id,
    customer_id, card_id}]``) and an authorization (``scenario_id`` + ``card_id`` +
    ``profile_id``). The served catalogue names no card, so these are the only sources.
    """
    found: list[ServedProfile] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                visit(value)
            return
        if not isinstance(node, dict):
            return
        scenario = node.get("scenario_id")
        if _text(scenario):
            bootstrap = served_profile({"profile": node}) if "profile_context" in node else None
            if bootstrap is not None:
                found.append(bootstrap)
            profiles = node.get("fixture_profiles")
            for p in profiles if isinstance(profiles, list) else []:
                if isinstance(p, dict) and _text(p.get("card_id")):
                    found.append(
                        ServedProfile(
                            scenario_id=scenario,
                            customer_id=p["customer_id"] if _text(p.get("customer_id")) else None,
                            card_id=p["card_id"],
                            profile_id=p["profile_id"] if _text(p.get("profile_id")) else None,
                        )
                    )
            if _text(node.get("card_id")):
                found.append(
                    ServedProfile(
                        scenario_id=scenario,
                        customer_id=node["customer_id"] if _text(node.get("customer_id")) else None,
                        card_id=node["card_id"],
                        profile_id=node["profile_id"] if _text(node.get("profile_id")) else None,
                    )
                )
        for value in node.values():
            if isinstance(value, (dict, list)):
                visit(value)

    visit(obj)
    return found


def served_scenario_ids(bootstrap: Any, reference: Any) -> list[str] | None:
    """The scenario ids the platform serves now: bootstrap ``scenarios``, else reference
    data ``runtime.scenario_ids``, else its ``scenario_catalogue``; None if neither says."""
    candidates: list[Any] = []
    if isinstance(bootstrap, dict):
        candidates.append(bootstrap.get("scenarios"))
    if isinstance(reference, dict):
        runtime = reference.get("runtime")
        candidates.append(runtime.get("scenario_ids") if isinstance(runtime, dict) else None)
        tables = reference.get("tables")
        candidates.append(tables.get("scenario_catalogue") if isinstance(tables, dict) else None)
    for candidate in candidates:
        if not isinstance(candidate, list) or not candidate:
            continue
        ids = [c.get("scenario_id") if isinstance(c, dict) else c for c in candidate]
        ids = [i for i in ids if _text(i)]
        if ids:
            return sorted(set(ids))
    return None


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


@dataclass(frozen=True)
class ReferenceShape:
    """What tells served reference data changed without syncing it: the pack version, the
    row count of every served table and the history file's metadata (``history``)."""

    pack_version: str | None
    counts: tuple[tuple[str, int], ...]
    history: str

    def drift(self, served: ReferenceShape) -> str | None:
        """Why ``served`` differs from this shape (the last one synced), or None."""
        if served.pack_version != self.pack_version:
            return f"pack_version {self.pack_version} -> {served.pack_version}"
        before, after = dict(self.counts), dict(served.counts)
        changed = [f"{t} {before.get(t, 0)} -> {after.get(t, 0)}" for t in sorted(before.keys() | after.keys())
                   if before.get(t, 0) != after.get(t, 0)]  # fmt: skip
        if changed:
            return "reference-data rows changed: " + ", ".join(changed)
        if served.history != self.history:
            return "the served history file changed"
        return None


def reference_shape(reference: Any) -> ReferenceShape:
    """The ``ReferenceShape`` of a ``/v1/reference-data`` reply."""
    ref = reference if isinstance(reference, dict) else {}
    tables = ref.get("tables") if isinstance(ref.get("tables"), dict) else {}
    counts = tuple(sorted((str(name), len(rows)) for name, rows in tables.items() if isinstance(rows, list)))
    version = ref.get("pack_version")
    return ReferenceShape(
        pack_version=str(version) if version is not None else None,
        counts=counts,
        history=json.dumps(ref.get("history"), sort_keys=True, default=str),
    )


def event_ids(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The reference ids a (valid) event names: its merchant, items, customer and cards."""
    auth, mandate = data["authorization"], data["mandate"]
    ids = [("merchant", auth["merchant"]["merchant_id"])]
    ids += [("item", line["item_id"]) for line in auth["items"]]
    ids += [("customer", mandate["customer_id"]), ("card", auth["card_id"]), ("card", mandate["card_id"])]
    return list(dict.fromkeys(ids))


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

    state: Literal["stopped", "starting", "standby", "polling", "degraded"]
    """``standby``: another process holds the worker lease and polls; this one does not."""
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


@dataclass(frozen=True)
class RunCounts:
    """A run's counters in the store: events received, decisions recorded, step-ups waiting."""

    delivered: int
    decided: int
    pending_human: int


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
    stored: RunCounts | None = None
    """The counters as last recomputed from the store (``_save_run``): they include what
    other processes, or this one before a restart, delivered and decided."""

    def status(self) -> RunStatus:
        stored = self.stored or RunCounts(0, 0, 0)
        delivered = max(len(self.live_ids), stored.delivered)
        return RunStatus(
            run_id=self.run_id,
            viseca_run_id=self.viseca_run_id,
            scenario_id=self.scenario_id,
            mandate_id=self.mandate_id,
            card_id=self.card_id,
            state=self.state,
            delivered=delivered,
            decided=max(len(self.decided), stored.decided),
            pending_human=max(len(self.pending), stored.pending_human),
            total=max(self.total, delivered),
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
        lease: WorkerLease | None = None,
        standby_retry_s: float = STANDBY_RETRY_S,
        lease_check_s: float = LEASE_CHECK_S,
        feed_sync_s: float = FEED_SYNC_S,
        reference_check_s: float = REFERENCE_CHECK_S,
        unknown_id_sync_s: float = UNKNOWN_ID_SYNC_S,
    ) -> None:
        self.client = client
        self._db_engine = db or get_engine()
        self._lease = lease if lease is not None else worker_lease(self._db_engine)
        self._standby_retry_s = standby_retry_s
        self._lease_check_s = lease_check_s
        self._lease_checked_at = 0.0
        self._feed_sync_s = feed_sync_s
        self._feed_synced_at = 0.0
        self._reference_check_s = reference_check_s
        self._reference_checked_at = 0.0
        self._unknown_id_sync_s = unknown_id_sync_s
        self._history = ReloadableHistory(history) if history is not None else None
        self._ledger = ledger
        self._provider = provider
        self._signals_enabled = signals_enabled
        self._budget_ms = budget_ms if budget_ms is not None else budget_ms_from_env()
        self._poll_wait_s = poll_wait_s
        self._poll_wait_max_s = poll_wait_s
        """The configured long-poll wait; the bootstrap's ``long_poll_max_seconds`` caps it."""
        self._waiting_step_up_pause_s = waiting_step_up_pause_s
        self._implementations = implementations
        self._stubbed = stubbed
        self._data_dir = data_dir
        self._now = now

        self.human_window_s = DEFAULT_HUMAN_WINDOW_S
        self.decision_deadline_s: float | None = None
        self.bootstrap: dict[str, Any] | None = None
        self.pack_version: str | None = None
        """The bootstrap's ``pack_version`` at its last read."""
        self._bootstrap_read_at: float | None = None
        self._bootstrap_task: asyncio.Task[None] | None = None
        self.reference_data: dict[str, Any] | None = None
        self.history_reseeded = False
        self.served_tables: list[seed_module.TableSync] = []
        """What the last reference sync did to each served reference table."""
        self.served_history_sha256: str | None = None
        """SHA-256 of the history file Viseca serves, once checked."""
        self.fx_rates_mismatch: list[str] | None = None
        """How the served fx rates differ from ``FX_TO_CHF`` (empty: equal); None until checked."""
        self.served_scenarios: list[str] | None = None
        """The scenario ids the platform serves now, as of the last reference sync."""
        self.reference_syncs = 0
        """Reference syncs finished since start (one at start, then any runtime ones)."""
        self._reference_seen: ReferenceShape | None = None
        """Pack version, row counts and history metadata of the last reference data synced."""
        self._sync_task: asyncio.Task[bool] | None = None
        self._known_ids: dict[str, frozenset[str]] | None = None
        """Merchant, item, customer and card ids in the store, as of the last sync."""
        self._unresolved_ids: set[tuple[str, str]] = set()
        """Ids an event named that a finished sync did not bring: they start no second sync."""
        self._background: set[asyncio.Task[Any]] = set()
        self._extras_seen: set[str] = set()
        """Event properties outside the schema already logged at warning level."""
        self._holder = False
        """True while this process holds the worker lease (only then it syncs and polls)."""
        self._profiles: dict[str, tuple[str, str, str | None]] = {}
        """scenario id → (customer, card, profile) as last stored, so a repeat sighting
        costs no store round trip."""

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
    def history(self) -> ReloadableHistory:
        """The history index every run, the ledger and the API read; a reference sync that
        changes the store replaces what it points at (``ReloadableHistory``)."""
        if self._history is None:
            raise RuntimeError("worker not started")
        return self._history

    @property
    def events_cursor(self) -> int | str:
        return self._cursor

    @property
    def poll_wait_s(self) -> float:
        """The long-poll wait in use: the configured one, capped by the bootstrap's."""
        return self._poll_wait_s

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
        """Follow a run from its creation, so its progress is read while nothing arrives.

        A run not seen before is a run start: ``/v1/bootstrap`` is re-read in the background
        unless it was just read (``BOOTSTRAP_FRESH_S``).
        """
        if viseca_run_id not in self._runs:
            self._run_started(viseca_run_id)
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
        """Read settings and reference data, load history, then poll if this process holds
        the worker lease; else stay in standby until it can take it.

        Only the lease holder recovers pending step-ups, reads the event feed and polls, so
        two processes on one store never both decide (``store/lease.py``).
        """
        if self._task is not None:
            return
        self._state = "starting"
        await self._load_settings("start")
        held = await self._try_lease()
        if held:
            await self._sync_reference("start")
        if self._history is None:
            self._history = ReloadableHistory(await asyncio.to_thread(self._load_history))
        if self._ledger is None:
            self._ledger = default_ledger(self._db_engine, self._history)
        if held:
            await self._take_over()
        else:
            self._state = "standby"
        self._task = asyncio.create_task(self._lead(held), name="viseca-worker")

    async def stop(self) -> None:
        """Stop polling and cancel expiry timers (pending step-ups are recovered on start).

        Waits up to ``STOP_DRAIN_S`` for the engine thread's current work, writes the rows
        of the runs it drove, then closes any ledger session still open, so no pooled
        connection outlives the worker, and gives the worker lease up.
        """
        leading = self._state in ("polling", "degraded")
        tasks = [
            t
            for t in [self._task, self._sync_task, self._bootstrap_task, *self._expiry.values(), *self._background]
            if t is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        self._expiry.clear()
        self._background.clear()
        self._holder = False
        self._state = "stopped"
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(self._pool, lambda: None), STOP_DRAIN_S
            )
        except TimeoutError:
            log.warning("the engine thread is still busy after %s s; closing its ledger session", STOP_DRAIN_S)
        if leading:
            await asyncio.to_thread(self._save_runs_while_held)
            for run in self._runs.values():
                if run.recorded_state in ("done", "error"):
                    run.recorded_final.set()
        close = getattr(self._ledger, "close", None)
        if callable(close):
            close()
        await asyncio.to_thread(self._lease.release)
        await self.client.drain()

    # The worker lease --------------------------------------------------------------------

    async def _try_lease(self) -> bool:
        try:
            held = await asyncio.to_thread(self._lease.acquire)
        except (SQLAlchemyError, OSError) as exc:
            self._note_error(f"worker lease unavailable: {type(exc).__name__}: {exc}")
            return False
        if not held and self._state != "standby":
            log.warning("another process holds the worker lease and polls Viseca; this worker stands by")
        self._lease_checked_at = time.monotonic()
        self._holder = held
        return held

    async def _lead(self, held: bool) -> None:
        """Poll while this process holds the lease; stand by while another one does."""
        while True:
            if not held:
                self._state = "standby"
                while not await self._try_lease():
                    await asyncio.sleep(self._standby_retry_s)
                log.info("took the worker lease; polling Viseca")
                await self._sync_reference("took the worker lease")
                await self._take_over()
            await self._loop()
            held = False
            await self._step_down()

    async def _take_over(self) -> None:
        """What only the lease holder does before its first poll (it may ``/resolve``)."""
        await self._recover_pending()
        await self._reconcile_platform()
        self._cursor = await asyncio.to_thread(self._load_cursor)
        await self._track_unfinished_runs()

    async def _step_down(self) -> None:
        """The lease is gone: stop closing step-ups here; the new holder recovers them."""
        log.warning("lost the worker lease; this worker stands by")
        self._holder = False
        tasks = [*self._expiry.values(), *self._background]
        if self._sync_task is not None:
            tasks.append(self._sync_task)
        self._expiry.clear()
        self._background.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._state = "standby"

    async def _lease_lost(self) -> bool:
        """At most every ``lease_check_s``: True once the lease no longer holds."""
        if time.monotonic() - self._lease_checked_at < self._lease_check_s:
            return False
        self._lease_checked_at = time.monotonic()
        try:
            return not await asyncio.to_thread(self._lease.held)
        except (SQLAlchemyError, OSError) as exc:
            self._note_error(f"worker lease check failed: {type(exc).__name__}: {exc}")
            return True

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

    async def remember_profiles(self, obj: Any, source: str) -> list[ServedProfile]:
        """Store every scenario → customer / card binding ``obj`` states (``profile_sightings``).

        Returns the bindings found, with the customer filled in from the card where the
        platform named only the card. A store failure is logged, never raised: a binding is
        operator data and must not stop a decision.
        """
        sightings = profile_sightings(obj)
        if not sightings:
            return []
        try:
            return await asyncio.to_thread(self._save_profiles, sightings, source)
        except Exception as exc:
            log.exception("storing scenario profiles failed")
            self._note_error(f"scenario profiles not stored: {type(exc).__name__}: {exc}")
            return []

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

    async def _load_settings(self, reason: str) -> bool:
        """Read ``/v1/bootstrap``: human window, decision deadline, long-poll wait and pack
        version. A value that changed since the last read is logged and used from now on,
        in every run. True when the pack version changed (not at the first read)."""
        try:
            bootstrap = await self.client.bootstrap()
        except VisecaError as exc:
            self._note_error(f"bootstrap failed ({reason}), keeping the timeouts in use: {exc}")
            return False
        self._bootstrap_read_at = time.monotonic()
        first = self.bootstrap is None
        self.bootstrap = bootstrap if isinstance(bootstrap, dict) else {}
        limits = self.bootstrap.get("limits")
        limits = limits if isinstance(limits, dict) else {}
        before = (self.human_window_s, self.decision_deadline_s, self._poll_wait_s, self.pack_version)
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
        self._poll_wait_s = min(self._poll_wait_max_s, long_poll) if long_poll is not None else self._poll_wait_max_s
        version = self.bootstrap.get("pack_version")
        self.pack_version = str(version) if version is not None else None
        after = (self.human_window_s, self.decision_deadline_s, self._poll_wait_s, self.pack_version)
        if first:
            log.info(
                "Viseca bootstrap: human window %s s, decision deadline %s s, long poll %s s, pack %s",
                *after,
            )
            return False
        names = ("human window (s)", "decision deadline (s)", "long-poll wait (s)", "pack_version")
        changes = [f"{n} {old} -> {new}" for n, old, new in zip(names, before, after, strict=True) if old != new]
        if changes:
            log.warning("Viseca bootstrap changed (%s): %s", reason, "; ".join(changes))
            for run in self._runs.values():
                if run.ctx is not None:
                    run.ctx.human_window_s = math.ceil(self.human_window_s)
        else:
            log.info("Viseca bootstrap re-read (%s): unchanged", reason)
        return before[3] != after[3]

    async def refresh_bootstrap(self, reason: str) -> None:
        """A run starts: re-read ``/v1/bootstrap`` (``_load_settings``); a new pack version
        syncs the reference data before this returns. Lease holder only; concurrent calls
        share one read. Never raises: a failure keeps the values in use."""
        if not self._holder:
            return
        task = self._bootstrap_task
        if task is None or task.done():
            task = self._bootstrap_task = asyncio.create_task(self._refresh_bootstrap(reason), name="bootstrap-read")
        await asyncio.shield(task)

    async def _refresh_bootstrap(self, reason: str) -> None:
        try:
            before = self.pack_version
            if await self._load_settings(reason):
                await self._sync_reference(f"pack_version {before} -> {self.pack_version}")
        except Exception as exc:
            log.exception("bootstrap re-read failed")
            self._note_error(f"bootstrap re-read ({reason}) failed: {type(exc).__name__}: {exc}")

    def _run_started(self, viseca_run_id: str) -> None:
        """A run seen for the first time: re-read the bootstrap in the background, unless it
        was read in the last ``BOOTSTRAP_FRESH_S`` (D3 reads it just before creating the run)."""
        fresh = self._bootstrap_read_at is not None and time.monotonic() - self._bootstrap_read_at < BOOTSTRAP_FRESH_S
        if fresh or not self._holder:
            return
        try:
            self._spawn(self.refresh_bootstrap(f"run {viseca_run_id} started"), "bootstrap-read")
        except RuntimeError:  # no running loop: nothing to schedule on
            return

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        """A background task the worker cancels when it stops or stands by."""
        task = asyncio.create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # Reference data ------------------------------------------------------------------------

    async def _sync_reference(self, reason: str, reference: dict[str, Any] | None = None) -> bool:
        """Sync the served reference data into the store (``_run_sync``); True if it changed.

        Lease holder only. A sync already running is joined, not repeated, so concurrent
        triggers coalesce. The sync runs on even when a waiter gives up (the bounded wait of
        an unknown id), and never raises: a failure is logged and decisions go on with what
        the store has.
        """
        if not self._holder:
            log.debug("reference sync (%s) skipped: this worker does not hold the lease", reason)
            return False
        task = self._sync_task
        if task is None or task.done():
            task = self._sync_task = asyncio.create_task(self._run_sync(reason, reference), name="reference-sync")
        else:
            log.info("reference sync (%s) joins the sync in flight", reason)
        return await asyncio.shield(task)

    async def _run_sync(self, reason: str, reference: dict[str, Any] | None) -> bool:
        started = time.perf_counter()
        log.info("reference sync (%s) started", reason)
        changed = False
        try:
            changed = await self._check_reference_data(reference)
            if changed or self._history is None:
                index = await asyncio.to_thread(self._load_history)
                if self._history is None:
                    self._history = ReloadableHistory(index)
                else:
                    self._history.replace(index)
                    log.info("history index reloaded after the reference sync")
            await self._remember_served()
        except Exception as exc:
            log.exception("reference sync failed")
            self._note_error(f"reference sync ({reason}) failed, keeping the stored reference data: {exc}")
        try:
            self._known_ids = await asyncio.to_thread(self._load_known_ids)
        except Exception as exc:
            log.exception("reading the known reference ids failed")
            self._note_error(f"known reference ids not read: {type(exc).__name__}: {exc}")
        self._reference_checked_at = time.monotonic()
        self.reference_syncs += 1
        log.info(
            "reference sync (%s) finished in %.0f ms: %s",
            reason,
            (time.perf_counter() - started) * 1000,
            "store changed" if changed else "store unchanged",
        )
        return changed

    async def _check_reference(self) -> None:
        """Every ``REFERENCE_CHECK_S``: sync when ``/v1/reference-data`` differs from the last
        sync in pack version, row counts or history file (``ReferenceShape.drift``)."""
        try:
            reference = await self.client.reference_data()
        except VisecaError as exc:
            log.warning("reference data check skipped: %s", exc)
            return
        if not isinstance(reference, dict):
            log.warning("reference data check skipped: the reply is not an object")
            return
        seen = self._reference_seen
        why = "no reference sync has succeeded yet" if seen is None else seen.drift(reference_shape(reference))
        if why is None:
            log.debug("reference data unchanged since the last sync")
            return
        log.warning("reference data changed (%s); syncing", why)
        await self._sync_reference(why, reference)

    def _unknown_ids(self, ids: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Those of ``ids`` the store does not know. None of them while the known ids were
        never read (nothing to compare with)."""
        known = self._known_ids
        if known is None:
            return []
        return [(kind, value) for kind, value in ids if value not in known[kind]]

    async def _reference_rows(self, data: Mapping[str, Any], deadline_at: datetime) -> list[EvidenceRow]:
        """The ids an event names that the store does not know: sync for them (unless a
        finished sync already failed to bring them), then an ``info`` row naming those still
        unknown. Missing is never a pass: no price range, no history, never familiar."""
        unknown = self._unknown_ids(event_ids(data))
        to_sync = [u for u in unknown if u not in self._unresolved_ids]
        if to_sync:
            await self._sync_for_unknown(to_sync, deadline_at)
        missing = self._unknown_ids(unknown)
        if not missing:
            return []
        shown = ", ".join(f"{kind} {value}" for kind, value in missing)
        return [
            EvidenceRow(
                rule="reference_data",
                outcome="info",
                detail=f"Not in the reference data: {shown}. Their catalogue prices and purchase history "
                "count as unknown, never as familiar.",
                source="history",
            )
        ]

    async def _sync_for_unknown(self, ids: list[tuple[str, str]], deadline_at: datetime) -> None:
        """Sync for ids the store does not know, waiting at most ``UNKNOWN_ID_SYNC_S`` (less
        when the deadline is near); the sync goes on in the background after that. Ids the
        finished sync did not bring start no further sync."""
        names = ", ".join(f"{kind} {value}" for kind, value in ids)
        left = (deadline_at - self._now()).total_seconds() - self._budget_ms / 1000 - POST_MARGIN_S
        wait = max(0.0, min(self._unknown_id_sync_s, left))
        log.warning("the store does not know %s; syncing the reference data (waiting up to %.1f s)", names, wait)
        sync = self._spawn(self._sync_reference(f"unknown {names}"), "reference-sync-wait")

        def settle(_: Any) -> None:
            unresolved = self._unknown_ids(ids)
            self._unresolved_ids.update(unresolved)
            if unresolved:
                log.warning(
                    "the reference data does not have %s either; decided without catalogue or history for them",
                    ", ".join(f"{kind} {value}" for kind, value in unresolved),
                )

        sync.add_done_callback(settle)
        try:
            await asyncio.wait_for(asyncio.shield(sync), wait)
        except TimeoutError:
            log.warning("the reference sync is still running after %.1f s; deciding with what the store has", wait)

    def _load_known_ids(self) -> dict[str, frozenset[str]]:
        columns = {
            "merchant": Merchant.merchant_id,
            "item": Item.item_id,
            "customer": Customer.customer_id,
            "card": Card.card_id,
        }
        with session(self._db_engine) as s:
            return {kind: frozenset(s.scalars(select(column))) for kind, column in columns.items()}

    async def _check_reference_data(self, reference: dict[str, Any] | None = None) -> bool:
        """Sync the served tables and check the served history file; True if the store changed.

        ``reference`` is a ``/v1/reference-data`` reply already read (the periodic check);
        without one it is read here. The history file is downloaded at the first sync and
        again only when the served ``history`` metadata or the pack version changed.
        """
        if reference is None:
            try:
                reference = await self.client.reference_data()
            except VisecaError as exc:
                self._note_error(f"reference data unavailable, keeping the stored reference data: {exc}")
                return False
        self.reference_data = reference
        self._check_fx_rates()
        seen, shape = self._reference_seen, reference_shape(reference)
        changed = await self._sync_served_tables()
        if seen is not None and (seen.pack_version, seen.history) == (shape.pack_version, shape.history):
            history_checked, reseeded = True, False
        else:
            history_checked, reseeded = await self._check_history()
        if changed is not None and history_checked:
            self._reference_seen = shape  # a later check compares with this; a failure is retried
        return bool(changed) or reseeded

    async def _check_history(self) -> tuple[bool, bool]:
        """Compare the served history file with the pack's; re-seed on a difference.
        Returns (checked, re-seeded)."""
        meta = find_history_metadata(self.reference_data)
        served = str(meta.get("sha256", "")).strip().lower() if meta else ""
        expected = self.served_history_sha256 or seed_module.pack_file_sha256(HISTORY_FILE, self._data_dir)
        if served and served == expected:
            self.served_history_sha256 = served
            log.info("Viseca history file matches the stored one (sha256 %s)", expected)
            return True, False
        try:
            text = await self.client.authorization_history_csv()
        except VisecaError as exc:
            self._note_error(f"history download failed, keeping the stored history: {exc}")
            return False, False
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if served and digest != served:
            self._note_error(
                f"downloaded history sha256 {digest} does not match the served {served}; not re-seeding"
            )
            return False, False
        if digest == expected:
            self.served_history_sha256 = digest
            log.info("Viseca history file matches the stored one (downloaded, sha256 %s)", expected)
            return True, False
        banner = "!" * 72
        log.warning(
            "%s\nVISECA SERVES A DIFFERENT HISTORY FILE: sha256 %s, the store has %s.\n"
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
            self._note_error(f"re-seeding history failed, keeping the stored history: {exc}")
            return False, False
        self.served_history_sha256 = digest
        self.history_reseeded = True
        log.warning(
            "%s\nRE-SEEDED authorization_history: %d rows from Viseca (sha256 %s)\n%s",
            banner,
            rows,
            digest,
            banner,
        )
        return True, True

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

    async def _sync_served_tables(self) -> bool | None:
        """Upsert the served reference tables before the history check, so a served history
        file's customers and cards already exist when it is re-seeded. True if one changed,
        None if nothing could be synced."""
        tables = self.reference_data.get("tables") if isinstance(self.reference_data, dict) else None
        if not isinstance(tables, dict):
            self._note_error("reference data serves no tables; keeping the stored reference tables")
            return None
        try:
            self.served_tables = await asyncio.to_thread(self._sync_served, tables)
        except Exception as exc:
            log.exception("syncing the served reference tables failed")
            self._note_error(f"served reference tables not synced, keeping the stored ones: {exc}")
            return None
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
        return any(t.changed for t in self.served_tables)

    def _sync_served(self, tables: dict[str, Any]) -> list[seed_module.TableSync]:
        with session(self._db_engine) as s:
            return seed_module.sync_served(s, tables)

    def _reseed_history(self, text: str) -> int:
        with session(self._db_engine) as s:
            return seed_module.reseed_history(s, text)

    def _load_history(self) -> StoreHistoryIndex:
        with session(self._db_engine) as s:
            return StoreHistoryIndex.load(s)

    async def _remember_served(self) -> None:
        """Store which scenarios the platform serves and the bootstrap profile's binding."""
        served = served_scenario_ids(self.bootstrap, self.reference_data)
        if served is not None:
            self.served_scenarios = served
            try:
                await asyncio.to_thread(self._save_state, SERVED_SCENARIOS_KEY, served)
            except Exception as exc:
                log.exception("storing the served scenarios failed")
                self._note_error(f"served scenarios not stored: {type(exc).__name__}: {exc}")
        profile = self.bootstrap.get("profile") if isinstance(self.bootstrap, dict) else None
        await self.remember_profiles(profile, "bootstrap")

    async def _reconcile_platform(self) -> None:
        """Read every platform authorization once at start.

        - Every run it lists says which card its scenario runs on (``remember_profiles``).
        - A step-up the platform still serves as waiting (``pending_step_up``) that our
          ledger already closed by the timeout rule never got its ``/resolve`` through
          (a restart or a failed call): it is sent now, ``decline`` with the timeout
          message, so the platform stops serving it (rules.md Q2). One the ledger does not
          know is another decider's and is left alone; one the customer answered is only
          logged, since a human answer is never re-sent on their behalf.
        """
        try:
            items = await self.client.list_authorizations()
        except VisecaError as exc:
            log.warning("platform authorizations unavailable at start: %s", exc)
            return
        if not isinstance(items, list):
            return
        await self.remember_profiles(items, "authorization")
        waiting = [
            i["authorization_id"]
            for i in items
            if isinstance(i, dict) and i.get("status") == STEP_UP_WAITING and _text(i.get("authorization_id"))
        ]
        async with self._resolution_lock:
            for live_id in waiting:
                entry = await self._engine(self.ledger.get, live_id)
                if entry is None or entry.outcome != "step_up" or not entry.final:
                    continue
                if entry.resolved_by != "timeout":
                    log.warning(
                        "Viseca still waits on step-up %s, which the ledger has %s by the %s; not re-sent",
                        live_id,
                        entry.uncertain_outcome,
                        entry.resolved_by,
                    )
                    continue
                if not self._claim_resolve(live_id):
                    continue
                try:
                    await self.client.resolve(
                        live_id, "decline", timeout_message(self.human_window_s), [_resolved_by_row("timeout")]
                    )
                    log.info("step-up %s expired in the ledger; its timeout decline is now at Viseca", live_id)
                except VisecaError as exc:
                    if exc.status == 409:
                        log.info("Viseca closed step-up %s itself (%s)", live_id, exc.code)
                    else:
                        self._note_error(f"timeout resolve of {live_id} at start failed: {exc}")

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
        """Poll and decide until the worker lease is lost (then return) or the task stops."""
        self._state = "polling"
        while True:
            if await self._lease_lost():
                return
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
                if time.monotonic() - self._feed_synced_at >= self._feed_sync_s:
                    await self._sync_events()
                if time.monotonic() - self._reference_checked_at >= self._reference_check_s:
                    self._reference_checked_at = time.monotonic()
                    self._spawn(self._check_reference(), "reference-check")
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
        await self._refresh_progress([run for run in self._runs.values() if run.state not in ("done", "error")])
        await self._sync_events()

    async def _refresh_progress(self, runs: list[RunState]) -> None:
        """Read each run's progress from ``GET /v1/scenario-runs/{id}`` and store its row.

        A run the platform no longer knows (404, e.g. after a team reset) is closed as
        ``error`` so it is not read again.
        """
        for run in runs:
            try:
                progress = await self.client.get_run(run.viseca_run_id)
            except VisecaError as exc:
                if exc.status != 404:
                    log.warning("progress of run %s unavailable: %s", run.viseca_run_id, exc)
                    continue
                run.state = "error"
                run.last_error = "Viseca no longer knows this run"
                run.finished_at = run.finished_at or self._now()
            else:
                self._apply_progress(run, progress)
                await self.remember_profiles(progress, "run")
            await self._persist_run(run)

    async def _track_unfinished_runs(self) -> None:
        """Follow the live runs the store still shows as unfinished, so their state is read
        from the platform again: a run that completed while no worker polled (or while
        another process decided it) is closed instead of staying ``running``."""
        rows = await asyncio.to_thread(self._unfinished_run_rows)
        runs = [self._track_row(row) for row in rows if row.viseca_run_id]
        if runs:
            log.info("following %d unfinished live run(s) from the store", len(runs))
            await self._refresh_progress(runs)

    def _track_row(self, row: Run) -> RunState:
        assert row.viseca_run_id is not None
        run = self._run(row.viseca_run_id)
        run.started_at = min(run.started_at, row.started_at.replace(tzinfo=row.started_at.tzinfo or UTC))
        run.scenario_id = run.scenario_id or row.scenario_id
        run.mandate_id = run.mandate_id or row.mandate_id or None
        run.card_id = run.card_id or row.card_id or None
        run.total = max(run.total, row.total)
        if run.state == "starting":
            run.state = "running"
        return run

    def _unfinished_run_rows(self) -> list[Run]:
        with session(self._db_engine) as s:
            return list(
                s.scalars(
                    select(Run).where(Run.kind == "live", Run.state.in_(("starting", "running")))
                )
            )

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
        check = check_event(data)
        if check.errors:
            await self._reject_invalid(envelope, check.errors)
            return
        assert isinstance(data, dict)
        received_at = self._now()
        auth = data["authorization"]
        live_id: str = auth["authorization_id"]
        if check.extras:
            self._log_extras(live_id, check.extras)
        deadline_at = _parse_time(data["deadline_at"]) or received_at
        viseca_run_id = str(envelope.get("run_id") or "")
        if viseca_run_id not in self._runs:
            self._run_started(viseca_run_id)
        waiting = envelope.get("status") == STEP_UP_WAITING  # decided already; served on every poll
        reference_rows = [] if waiting else await self._reference_rows(data, deadline_at)
        bound = viseca_run_id in self._runs and self._runs[viseca_run_id].ctx is not None
        run = self._bind_run(viseca_run_id, data)
        if not bound:
            # The ledger reads runs.kind (live) to carry the session watch and remembered
            # answers over from earlier live runs, so the row is written before the first
            # decision; a run with no row is treated as a replay.
            await self._persist_run(run)
            await self.remember_profiles(auth, "authorization")
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
        extra.extend(reference_rows)
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

    def _log_extras(self, live_id: str, extras: list[str]) -> None:
        """Properties the event schema does not list are passed on unread: logged loudly the
        first time each one is seen, then quietly."""
        new = [p for p in extras if p not in self._extras_seen]
        self._extras_seen.update(new)
        if new:
            log.warning("request %s carries properties the event schema does not list, passed on unread: %s",
                        live_id, ", ".join(new))  # fmt: skip
        else:
            log.debug("request %s carries unlisted properties: %s", live_id, ", ".join(extras))

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
        self._feed_synced_at = time.monotonic()
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
        await self.remember_profiles(items, "run")
        completed: list[str] = []
        async with self._resolution_lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "scenario.completed" and isinstance(item.get("run_id"), str):
                    completed.append(item["run_id"])
                else:
                    await self._check_feed_item(item)
        await self._close_completed(completed)
        if next_cursor is not None and next_cursor != self._cursor:
            await asyncio.to_thread(self._save_cursor, next_cursor)
            self._cursor = next_cursor

    async def _close_completed(self, viseca_run_ids: list[str]) -> None:
        """The feed says these runs completed: read their state and close the ones we know
        (followed here, or stored as live runs), so no row stays ``running``."""
        runs: list[RunState] = []
        for viseca_run_id in dict.fromkeys(viseca_run_ids):
            run = self._runs.get(viseca_run_id)
            if run is None:
                row = await asyncio.to_thread(self._live_run_row, viseca_run_id)
                run = self._track_row(row) if row is not None else None
            if run is not None and run.state not in ("done", "error"):
                runs.append(run)
        await self._refresh_progress(runs)

    def _live_run_row(self, viseca_run_id: str) -> Run | None:
        with session(self._db_engine) as s:
            return s.scalar(select(Run).where(Run.viseca_run_id == viseca_run_id, Run.kind == "live"))

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
        """Write the run's row. Its counters are recomputed from the store (``events_raw``
        and the ledger) and ``started_at`` keeps its first write, so a restart or a second
        process never resets them; ``run.stored`` takes the recomputed counters, and
        ``run.recorded_state`` the state once the row has committed."""
        with self._run_write_lock:
            run.recorded_state = self._write_run_row(run)

    def _write_run_row(self, run: RunState) -> Literal["starting", "running", "done", "error"]:
        """The run's row, committed on return; returns the state it wrote."""
        written = run.state
        with session(self._db_engine) as s:
            counts = self._run_counts(s, run.run_id)
            run.stored = counts
            status = run.status()
            row = s.get(Run, run.run_id)
            if row is None:
                s.add(
                    Run(
                        run_id=run.run_id,
                        viseca_run_id=run.viseca_run_id,
                        kind="live",
                        scenario_id=run.scenario_id,
                        mandate_id=run.mandate_id or run.viseca_mandate_id or "",
                        card_id=run.card_id or "",
                        state=written,
                        delivered=counts.delivered,
                        decided=counts.decided,
                        pending_human=counts.pending_human,
                        total=status.total,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        worker_last_poll_at=self._last_poll_at,
                        last_error=run.last_error,
                    )
                )
                return written
            row.viseca_run_id = row.viseca_run_id or run.viseca_run_id
            row.scenario_id = row.scenario_id or run.scenario_id
            row.mandate_id = run.mandate_id or run.viseca_mandate_id or row.mandate_id
            row.card_id = run.card_id or row.card_id
            row.state = written
            row.delivered = counts.delivered
            row.decided = counts.decided
            row.pending_human = counts.pending_human
            row.total = max(row.total, status.total)
            row.finished_at = run.finished_at
            row.worker_last_poll_at = self._last_poll_at or row.worker_last_poll_at
            row.last_error = run.last_error
        return written

    def _save_runs_while_held(self) -> None:
        """At stop: write every run's row once more. The loop writes a row a step after it
        changes the run in memory (after the feed read that follows a decision, after the
        profiles of a closed run), so a stop in between would leave the store behind what
        this worker decided, and D7 after a restart reads the row. Only while this process
        still holds the lease: after that the rows are the holder's."""
        try:
            if not self._lease.held():
                return
            for run in list(self._runs.values()):
                self._save_run(run)
        except Exception as exc:
            log.exception("writing the run rows at stop failed")
            self._note_error(f"run rows not written at stop: {type(exc).__name__}: {exc}")

    def _run_counts(self, s: Session, run_id: str) -> RunCounts:
        """Events received for the run (``events_raw``); decisions recorded and step-ups
        still waiting (the ``decisions`` rows, or the ledger when it is not the store)."""
        live_ids = list(s.scalars(select(EventRaw.live_authorization_id).where(EventRaw.run_id == run_id)))
        if isinstance(self._ledger, ScopedStoreLedger | StoreLedger) or self._ledger is None:
            decided, pending = s.execute(
                select(
                    func.count(),
                    func.count().filter(Decision.outcome == "step_up", Decision.final.is_(False)),
                ).where(Decision.run_id == run_id)
            ).one()
            return RunCounts(len(live_ids), int(decided), int(pending))
        entries = [e for e in (self._ledger.get(i) for i in live_ids) if e is not None]
        waiting = sum(e.outcome == "step_up" and not e.final for e in entries)
        return RunCounts(len(live_ids), len(entries), waiting)

    def _load_cursor(self) -> int | str:
        with session(self._db_engine) as s:
            row = s.get(WorkerState, EVENTS_CURSOR_KEY)
        if row is None or not isinstance(row.value, int | str) or isinstance(row.value, bool):
            log.info("no stored event feed cursor; reading the feed from 0")
            return 0
        log.info("event feed cursor resumed at %s", row.value)
        return row.value

    def _save_cursor(self, cursor: int | str) -> None:
        self._save_state(EVENTS_CURSOR_KEY, cursor)

    def _save_state(self, key: str, value: Any) -> None:
        with session(self._db_engine) as s:
            s.merge(WorkerState(key=key, value=value, updated_at=self._now()))

    def _save_profiles(self, sightings: list[ServedProfile], source: str) -> list[ServedProfile]:
        """Upsert ``scenario_profiles``, the last sighting of a scenario winning. A row is
        written only when the binding is new or changed."""
        latest = {p.scenario_id: p for p in sightings}
        saved: list[ServedProfile] = []
        with session(self._db_engine) as s:
            for scenario_id, p in latest.items():
                customer = s.scalar(
                    select(Account.customer_id)
                    .join(Card, Card.account_id == Account.account_id)
                    .where(Card.card_id == p.card_id)
                ) or p.customer_id
                if customer is None:
                    log.warning("scenario %s runs on card %s, which the store does not know", scenario_id, p.card_id)
                    continue
                known = self._profiles.get(scenario_id)
                binding = (customer, p.card_id, p.profile_id or (known[2] if known else None))
                saved.append(ServedProfile(scenario_id, customer, p.card_id, binding[2]))
                if known == binding:
                    continue
                row = s.get(ScenarioProfile, scenario_id)
                if row is None or (row.customer_id, row.card_id, row.profile_id) != binding:
                    if row is None:
                        row = ScenarioProfile(scenario_id=scenario_id)
                        s.add(row)
                    row.customer_id, row.card_id, row.profile_id = binding
                    row.source = source
                    row.seen_at = self._now()
                    log.info("scenario %s runs on customer %s, card %s (%s)", scenario_id, customer, p.card_id, source)
                self._profiles[scenario_id] = binding
        return saved

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
