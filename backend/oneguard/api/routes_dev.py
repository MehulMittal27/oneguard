"""Operator endpoints D1–D6 (docs/api-contract.md §1.2). Never called by the UI.

This module and ``replay/`` are the only places that know about scenarios (CLAUDE.md
rule 3): which customer and card a scenario runs on, and its events. D1/D2 replay the
data pack offline through the same engine and ledger as live runs; D3/D4 start and
follow a Viseca run; D5 switches the models off or on; D6 shows the ledger itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from functools import cache

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from oneguard.api import models as api
from oneguard.api import policies, queries
from oneguard.api.errors import ApiError, not_found
from oneguard.api.offline import live_ids
from oneguard.api.routes_customer import reply, services
from oneguard.api.services import ScenarioBinding, Services
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import CompiledDraft, Policy
from oneguard.replay.events import Pack, build_events
from oneguard.store.schema import Run
from oneguard.viseca.client import RUNS_DISABLED_MESSAGE, runs_allowed
from oneguard.viseca.worker import first_value

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dev")

REPLAY_MANDATE = "TM_REPLAY"
"""The platform mandate id replay events carry when the policy has none at Viseca."""


@cache
def pack() -> Pack:
    return Pack.load()


def scenario_bindings(data: Pack | None = None) -> dict[str, list[ScenarioBinding]]:
    """customer id → the scenarios that run on their cards (C12 ``scenario_ids``)."""
    data = data or pack()
    found: dict[str, dict[str, str]] = defaultdict(dict)
    for row in data.attempts:
        authority = data.authorities[row["authority_id"]]
        found[authority["customer_id"]].setdefault(row["scenario_id"], authority["card_id"])
    return {
        customer: [ScenarioBinding(scenario_id=sid, card_id=card) for sid, card in sorted(scenarios.items())]
        for customer, scenarios in found.items()
    }


def _scenario(scenario_id: str, card_id: str) -> tuple[str, str]:
    """(customer id, card id) of the scenario; 404 unknown, 422 another card."""
    if scenario_id not in pack().scenarios:
        raise not_found(f"No scenario {scenario_id}.")
    for customer, bindings in scenario_bindings().items():
        for binding in bindings:
            if binding.scenario_id == scenario_id:
                if binding.card_id != card_id:
                    raise ApiError(
                        422, "validation", f"Scenario {scenario_id} runs on card {binding.card_id}, not {card_id}."
                    )
                return customer, card_id
    raise not_found(f"Scenario {scenario_id} has no purchases.")


# D1, D2 ---------------------------------------------------------------------------------


@router.get("/replay", response_model=api.ReplayStatus)
async def replay_status(request: Request) -> JSONResponse:
    """D1."""
    status = services(request).offline.status()
    if status is None:
        raise not_found("No replay has run yet.")
    return reply(status)


async def _replay_policy(s: Services, scenario_id: str, card_id: str) -> tuple[Policy, str]:
    """The card's active policy, else the scenario's instruction compiled for this replay
    only (never stored as a mandate). Returns the policy and the platform mandate id."""
    row = await s.db(queries.latest_mandate, s.db_engine, card_id)
    if row is not None and row.status == "active":
        rules, flags = policies.load_rules(row.rules, row.checks)
        policy = policies.policy_of(row.mandate_id, row.status, row.instruction, rules, flags, row.uncertainty_policy)
        return policy, row.viseca_mandate_id or REPLAY_MANDATE
    instruction = pack().scenarios[scenario_id]["cardholder_instruction"]
    compile_instruction = s.functions["compile_instruction"]
    try:
        draft: CompiledDraft = await asyncio.wait_for(
            asyncio.to_thread(compile_instruction, instruction, s.history, card_id, s.provider),
            s.compile_timeout_s,
        )
    except TimeoutError:
        raise ApiError(504, "compiler_timeout", "Compiling the scenario's instruction took too long.") from None
    policy = Policy(
        mandate_id=f"replay-{scenario_id}",
        status="active",
        instruction=draft.instruction,
        rules=draft.rules,
        uncertainty_policy=draft.uncertainty_policy,
        **policies.flags_of(draft),
    )
    return policy, REPLAY_MANDATE


@router.post("/replay/restart", response_model=api.ReplayStatus)
async def replay_restart(body: api.ReplayRestartRequest, request: Request) -> JSONResponse:
    """D2: replay a scenario's purchases offline, ``speed_ms`` apart."""
    s = services(request)
    customer_id, card_id = _scenario(body.scenario_id, body.card_id)
    policy, platform_mandate = await _replay_policy(s, body.scenario_id, card_id)
    sources = [row["authorization_id"] for row in pack().attempts_for(body.scenario_id)]
    events = build_events(
        pack(), body.scenario_id, mandate_id=platform_mandate, live_id=live_ids(sources).__getitem__
    )
    models = s.replay_models()
    status = await s.offline.restart(
        scenario_id=body.scenario_id,
        card_id=card_id,
        customer_id=customer_id,
        policy=policy,
        events=events,
        history=s.history,
        provider=s.decision_provider(models),
        signals_enabled=models,
        speed_ms=body.speed_ms,
    )
    return reply(status)


# D3, D4 ---------------------------------------------------------------------------------


@router.post("/runs", response_model=api.LiveRun)
async def create_run(body: api.CreateRunRequest, request: Request) -> JSONResponse:
    """D3: a Viseca run under the card's active policy, followed by the worker.

    Refused with 409 ``runs_disabled`` before anything else while ``ONEGUARD_ALLOW_RUNS=false``.
    """
    if not runs_allowed():
        raise ApiError(409, "runs_disabled", RUNS_DISABLED_MESSAGE)
    s = services(request)
    _scenario(body.scenario_id, body.card_id)
    row = await s.db(queries.latest_mandate, s.db_engine, body.card_id)
    if row is None or row.status != "active" or not row.viseca_mandate_id:
        raise ApiError(409, "validation", "The card needs an active policy confirmed at Viseca first.")
    if s.worker is None or s.client is None:
        raise ApiError(503, "upstream_unavailable", "The payment platform is not connected.")
    started = await s.viseca(s.client.create_run(body.scenario_id, row.viseca_mandate_id), "new run")
    run_id = str(started["run_id"])
    total = first_value(started, "generated_event_count", "total", "total_events", "event_count")
    s.live_started[run_id] = s.now()
    s.worker.track_run(
        run_id,
        scenario_id=body.scenario_id,
        viseca_mandate_id=row.viseca_mandate_id,
        total=total if isinstance(total, int) and not isinstance(total, bool) else None,
    )
    live = s.worker.live_run(run_id)
    assert live is not None
    return reply(live.model_copy(update={"card_id": live.card_id or body.card_id, "mandate_id": row.mandate_id}))


def _stored_live_run(run_id: str, row: Run) -> api.LiveRun:
    state = row.state if row.state in ("starting", "running", "done", "error") else "error"
    return api.LiveRun(
        run_id=run_id,
        scenario_id=row.scenario_id or "",
        card_id=row.card_id,
        mandate_id=row.mandate_id,
        state=state,
        delivered=row.delivered,
        decided=row.decided,
        pending_human=row.pending_human,
        total=row.total,
        worker_ok=False,
        last_error=row.last_error,
    )


@router.get("/runs/current", response_model=api.LiveRun | api.ReplayStatus)
async def current_run(request: Request) -> JSONResponse:
    """D7: the newest run, live or replay, as D4's ``LiveRun`` or D1's ``ReplayStatus``.

    Newest by the real time it started, across the stored runs, the replay this process
    runs and the runs D3 started that the worker has not stored yet. Starts nothing.
    """
    s = services(request)
    candidates: list[tuple[datetime, str, str]] = []  # (started, kind, run id: Viseca's for live)
    row = await s.db(queries.newest_run, s.db_engine)
    if row is not None:
        candidates.append((row.started_at, row.kind, row.viseca_run_id or row.run_id))
    replay = s.offline.current()
    if replay is not None:
        candidates.append((replay[1], "replay", replay[0]))
    candidates.extend((at, "live", run_id) for run_id, at in s.live_started.items())
    if not candidates:
        raise not_found("No run has started yet.")
    _, kind, run_id = max(candidates, key=lambda c: c[0])
    if kind == "live":
        live = s.worker.live_run(run_id) if s.worker is not None else None
        if live is not None:
            return reply(live)
        stored = await s.db(queries.live_run_row, s.db_engine, run_id)
        if stored is None:
            raise not_found(f"No run {run_id}.")
        return reply(_stored_live_run(run_id, stored))
    if replay is not None and replay[0] == run_id:
        status = s.offline.status()
        assert status is not None
        return reply(status)
    stored = await s.db(queries.run_row, s.db_engine, run_id)
    if stored is None:
        raise not_found(f"No run {run_id}.")
    return reply(
        api.ReplayStatus(
            scenario_id=stored.scenario_id or "",
            card_id=stored.card_id,
            delivered=stored.delivered,
            total=stored.total,
            running=False,
            next_at=None,
        )
    )


@router.get("/runs/{run_id}", response_model=api.LiveRun)
async def get_run(run_id: str, request: Request) -> JSONResponse:
    """D4: the worker's view of a live run, else the stored one."""
    s = services(request)
    live = s.worker.live_run(run_id) if s.worker is not None else None
    if live is not None:
        return reply(live)
    row = await s.db(queries.live_run_row, s.db_engine, run_id)
    if row is None:
        raise not_found(f"No run {run_id}.")
    return reply(_stored_live_run(run_id, row))


# D5 -------------------------------------------------------------------------------------


@router.post("/soft-signals", response_model=api.SoftSignalsToggle)
async def soft_signals(body: api.SoftSignalsToggle, request: Request) -> JSONResponse:
    """D5 chaos toggle: soft signals and the tier-2/3 provider, live and replay, at once."""
    s = services(request)
    s.set_models(body.enabled)
    log.warning("models switched %s by an operator", "on" if body.enabled else "off")
    return reply(api.SoftSignalsToggle(enabled=body.enabled))


# D6 -------------------------------------------------------------------------------------

_NOTES = {
    ("approve", None): "Approved: counts as spend.",
    ("decline", None): "Declined: not counted.",
    ("step_up", "pending"): "Waiting for the customer: reserved, not spent.",
    ("step_up", "approved"): "Approved by the customer: counts as spend.",
    ("step_up", "declined"): "Declined by the customer: not counted.",
    ("step_up", "expired"): "No answer in time: declined, not counted.",
}
_DECISION = {"approve": "approved", "decline": "stopped", "step_up": "uncertain"}


def _frozen(s: Services, last: LedgerEntry, period_days: int | None) -> bool:
    ledger = StoreLedger(Session(s.db_engine, expire_on_commit=False), history=s.history)
    try:
        view = ledger.view(
            run_id=last.run_id,
            customer_id=last.customer_id,
            card_id=last.card_id,
            at=last.ts_sim + timedelta(microseconds=1),
            period_days=period_days,
        )
        return view.frozen
    finally:
        ledger.session.close()


@router.get("/ledger/{card_id}", response_model=api.LedgerSnapshot)
async def ledger_snapshot(card_id: str, request: Request) -> JSONResponse:
    """D6: the ledger of the card's latest run, as the engine counts it."""
    s = services(request)
    if await s.db(queries.card_customer, s.db_engine, card_id) is None:
        raise not_found(f"No card {card_id}.")
    entries = await s.db(queries.latest_run_decisions, s.db_engine, card_id=card_id)
    mandate = await s.db(queries.latest_mandate, s.db_engine, card_id)
    rules = policies.load_rules(mandate.rules, mandate.checks)[0] if mandate else []
    mandate_id = entries[-1].mandate_id if entries else (mandate.mandate_id if mandate else "")
    if mandate is not None and mandate.mandate_id != mandate_id:
        rules = []
    usage = policies.usage(rules, entries, entries[-1].ts_sim if entries else s.now())
    frozen = False
    if entries:
        period = policies.period_limit(rules)
        frozen = await s.db(_frozen, s, entries[-1], period[1] if period else None)
    return reply(
        api.LedgerSnapshot(
            card_id=card_id,
            mandate_id=mandate_id,
            entries=[
                api.LedgerSnapshotEntry(
                    authorization_id=e.live_authorization_id,
                    occurred_at=e.ts_sim,
                    decision=_DECISION[e.outcome],
                    counted_chf=e.spent_chf,
                    note=_NOTES.get((e.outcome, e.uncertain_outcome), e.message),
                )
                for e in entries
            ],
            period_spent_chf=usage.period_spent_chf,
            frozen=frozen,
        )
    )
