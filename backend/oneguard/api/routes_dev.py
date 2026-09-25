"""Operator endpoints D1–D8 (docs/api-contract.md §1.2). Never called by the UI.

This module and ``replay/`` are the only places that know about scenarios (CLAUDE.md
rule 3): which customer and card a scenario runs on, and its events. D1/D2 replay the
data pack offline through the same engine and ledger as live runs; D3/D4 start and
follow a Viseca run; D5 switches the models off or on; D6 shows the ledger itself.

D2 replays only what the local pack has purchases for. D3 accepts any scenario in the
store's catalogue, which the worker syncs from ``/v1/reference-data`` at start, so the
scenarios Viseca serves (a judging pack the local ``data/`` lacks) can be run on any
card in the store; a scenario whose card is known (the pack's, or one the platform named:
its bootstrap profile, a run's ``fixture_profiles``, an authorization) must run on that
card. D8 lists every scenario with that binding, so ``make demo-live`` knows whom to sign
in as.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from functools import cache

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from oneguard.api import models as api
from oneguard.api import policies, queries
from oneguard.api.errors import ApiError, not_found
from oneguard.api.offline import live_ids
from oneguard.api.routes_customer import (
    reissue_passport,
    reply,
    revoke_at_platform,
    services,
)
from oneguard.api.services import ScenarioBinding, Services
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import CompiledDraft, Policy
from oneguard.passport import devices as device_store
from oneguard.replay.events import Pack, build_events
from oneguard.store.db import session
from oneguard.store.schema import Run, ScenarioCatalogue
from oneguard.store.seed import ENV_VAR
from oneguard.viseca.client import RUNS_DISABLED_MESSAGE, VisecaError, runs_allowed
from oneguard.viseca.worker import PLATFORM_PENDING, first_value, run_finished

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


def _catalogued(db: Engine, scenario_id: str) -> bool:
    with session(db) as s:
        return s.get(ScenarioCatalogue, scenario_id) is not None


async def _live_scenario(s: Services, scenario_id: str, card_id: str) -> str:
    """D3: the card's customer; 404 unknown scenario or card, 422 the scenario's card is another."""
    if not await s.db(_catalogued, s.db_engine, scenario_id):
        raise not_found(f"No scenario {scenario_id}.")
    cards = {b.card_id for bindings in (await s.bindings()).values() for b in bindings if b.scenario_id == scenario_id}
    if cards and card_id not in cards:
        raise ApiError(422, "validation", f"Scenario {scenario_id} runs on card {min(cards)}, not {card_id}.")
    customer_id = await s.db(queries.card_customer, s.db_engine, card_id)
    if customer_id is None:
        raise not_found(f"No card {card_id}.")
    return customer_id


def _scenario(scenario_id: str, card_id: str) -> tuple[str, str]:
    """D2: (customer id, card id) of a pack scenario; 404 unknown, 422 another card."""
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

    The worker re-reads ``/v1/bootstrap`` before the run is created (human window, decision
    deadline, long-poll wait; a new pack version syncs the reference data first).

    Refused with 409 ``runs_disabled`` before anything else while ``ONEGUARD_ALLOW_RUNS=false``,
    and, unless ``force``, with 409 ``run_active`` while the worker still follows an
    unfinished run (one run at a time: the customer answers one run's step-ups) or the
    scenario has a run in progress anywhere (``active_runs``). The platform's reply names the card
    the run uses (``fixture_profiles``); that binding is stored, and when it is another
    card than the one asked for (a scenario never run before, whose card nobody knew) the
    policy moves to that card (``queries.move_mandate``), so its holder sees it. The reply
    names the card's holder (``customer_id``, ``customer_name``).
    """
    if not runs_allowed():
        raise ApiError(409, "runs_disabled", RUNS_DISABLED_MESSAGE)
    s = services(request)
    if not body.force:
        await _refuse_while_running(s, body.scenario_id)
    await _live_scenario(s, body.scenario_id, body.card_id)
    row = await s.db(queries.latest_mandate, s.db_engine, body.card_id)
    if row is None or row.status != "active" or not row.viseca_mandate_id:
        raise ApiError(409, "validation", "The card needs an active policy confirmed at Viseca first.")
    if s.worker is None or s.client is None:
        raise ApiError(503, "upstream_unavailable", "The payment platform is not connected.")
    await s.worker.refresh_bootstrap("run start")
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
    profiles = [p for p in await s.worker.remember_profiles(started, "run") if p.scenario_id == body.scenario_id]
    card_id = profiles[0].card_id if profiles else body.card_id
    mandate_id = row.mandate_id
    if card_id != body.card_id and profiles and profiles[0].customer_id:
        mandate_id = await _move_policy(s, row.mandate_id, card_id, profiles[0].customer_id)
    live = s.worker.live_run(run_id)
    assert live is not None
    return reply(
        await _with_customer(s, live.model_copy(update={"card_id": live.card_id or card_id, "mandate_id": mandate_id}))
    )


async def active_runs(s: Services) -> dict[str, str]:
    """scenario id → one of its runs still in progress, by the Viseca run id.

    In progress: a run the worker follows that is not finished; a live run the store last
    saw starting or running, unless the platform's progress says it is over; a run with a
    purchase still open at the platform (awaiting a decision or a step-up answer), whoever
    started it. An unreadable platform leaves the store's word standing.
    """
    found: dict[str, str] = {}
    if s.worker is not None:
        for r in s.worker.status().runs:
            if r.state in ("starting", "running") and r.scenario_id:
                found.setdefault(r.scenario_id, r.viseca_run_id)
    for row in await s.db(queries.unfinished_live_runs, s.db_engine):
        if not row.scenario_id or not row.viseca_run_id or row.scenario_id in found:
            continue
        if s.client is not None:
            try:
                if run_finished(await s.platform(s.client.get_run(row.viseca_run_id))):
                    continue
            except VisecaError as exc:
                log.warning("progress of run %s unavailable: %s", row.viseca_run_id, exc)
        found[row.scenario_id] = row.viseca_run_id
    if s.client is not None:
        try:
            items = await s.platform(s.client.list_authorizations())
        except VisecaError as exc:
            log.warning("platform authorizations unavailable: %s", exc)
            items = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("status") not in PLATFORM_PENDING:
                continue
            scenario_id, run_id = item.get("scenario_id"), item.get("run_id")
            if isinstance(scenario_id, str) and isinstance(run_id, str):
                found.setdefault(scenario_id, run_id)
    return found


async def _refuse_while_running(s: Services, scenario_id: str) -> None:
    """D3 without ``force``: 409 ``run_active`` naming the run in progress."""
    followed = [r for r in s.worker.status().runs if r.state in ("starting", "running")] if s.worker else []
    if followed:
        run = followed[0]
        raise ApiError(
            409,
            "run_active",
            f"Run {run.viseca_run_id} ({run.scenario_id or 'unknown scenario'}) is still running: "
            f"{run.decided}/{run.total} decided, {run.pending_human} waiting for the customer.",
            {"run_id": run.viseca_run_id, "scenario_id": run.scenario_id},
        )
    run_id = (await active_runs(s)).get(scenario_id)
    if run_id is not None:
        raise ApiError(
            409,
            "run_active",
            f"{scenario_id} already has run {run_id} in progress (running, or purchases still open at the "
            "platform). Send force: true to start another anyway.",
            {"run_id": run_id, "scenario_id": scenario_id},
        )


async def _move_policy(s: Services, mandate_id: str, card_id: str, customer_id: str) -> str:
    """The run's card differs from the policy's: move the policy there (D3)."""
    async with s.policy_lock:
        source_card = [m.card_id for m in (await s.db(queries.mandates_by_id, s.db_engine, [mandate_id])).values()]
        moved, replaced = await s.db(
            queries.move_mandate, s.db_engine, mandate_id, card_id, customer_id, s.now(), f"md_{secrets.token_hex(8)}"
        )
        log.info("the platform runs mandate %s on card %s; policy moved there as %s", mandate_id, card_id, moved.mandate_id)
        for old in replaced:
            s.bind_mandate(old)
            if old.viseca_mandate_id and old.viseca_mandate_id != moved.viseca_mandate_id:
                await revoke_at_platform(s, old, strict=False)
        s.bind_mandate(moved, moved=True)
        for card in dict.fromkeys([card_id, *source_card]):
            await reissue_passport(s, card)
    return moved.mandate_id


async def _with_customer(s: Services, live: api.LiveRun) -> api.LiveRun:
    """``live`` with the holder of its card, when the store knows the card."""
    if not live.card_id:
        return live
    customer_id = await s.db(queries.card_customer, s.db_engine, live.card_id)
    if customer_id is None:
        return live
    names = await s.db(queries.customer_names, s.db_engine, [customer_id])
    return live.model_copy(update={"customer_id": customer_id, "customer_name": names.get(customer_id)})


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
            return reply(await _with_customer(s, live))
        stored = await s.db(queries.live_run_row, s.db_engine, run_id)
        if stored is None:
            raise not_found(f"No run {run_id}.")
        return reply(await _with_customer(s, _stored_live_run(run_id, stored)))
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
        return reply(await _with_customer(s, live))
    row = await s.db(queries.live_run_row, s.db_engine, run_id)
    if row is None:
        raise not_found(f"No run {run_id}.")
    return reply(await _with_customer(s, _stored_live_run(run_id, row)))


# D8 -------------------------------------------------------------------------------------


@router.get("/scenarios", response_model=api.ScenariosResponse)
async def list_scenarios(request: Request) -> JSONResponse:
    """D8: every scenario in the store's catalogue, whether the platform serves it now, the
    customer and card it runs on when known (``Services.bindings``) and a run of it still in
    progress (``active_runs``). ``make demo-live`` compiles the instruction listed here, so
    the worker re-reads ``/v1/bootstrap`` first: a new pack version syncs the catalogue
    before it is read. Changes nothing else."""
    s = services(request)
    if s.worker is not None:
        await s.worker.refresh_bootstrap("scenario list")
    catalogue = await s.db(queries.scenario_catalogue, s.db_engine)
    served = await s.db(queries.served_scenarios, s.db_engine)
    active = await active_runs(s)
    bound = {b.scenario_id: (customer, b) for customer, bs in (await s.bindings()).items() for b in bs}
    names = await s.db(queries.customer_names, s.db_engine, [c for c, _ in bound.values()])
    scenarios = []
    for row in catalogue:
        found = bound.get(row.scenario_id)
        profile = None
        if found is not None:
            customer, binding = found
            profile = api.ScenarioProfile(
                customer_id=customer,
                name=names.get(customer, customer),
                card_id=binding.card_id,
                profile_id=binding.profile_id,
                source=binding.source,  # type: ignore[arg-type]
            )
        scenarios.append(
            api.Scenario(
                scenario_id=row.scenario_id,
                scenario_name=row.scenario_name,
                cardholder_instruction=row.cardholder_instruction,
                served=served is not None and row.scenario_id in served,
                profile=profile,
                active_run_id=active.get(row.scenario_id),
            )
        )
    return reply(api.ScenariosResponse(scenarios=scenarios))


# D5 -------------------------------------------------------------------------------------


@router.get("/soft-signals", response_model=api.SoftSignalsState)
async def soft_signals_state(request: Request) -> JSONResponse:
    """D5 read: what the models are doing now, so the operator strip starts from the truth."""
    s = services(request)
    return reply(api.SoftSignalsState(live=s.live_models(), replay=s.replay_models()))


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
    rules, flags = policies.load_rules(mandate.rules, mandate.checks) if mandate else ([], {})
    mandate_id = entries[-1].mandate_id if entries else (mandate.mandate_id if mandate else "")
    if mandate is not None and mandate_id not in await s.db(queries.policy_lineage, s.db_engine, mandate.mandate_id):
        rules, flags = [], {}
    marked = await s.db(queries.requested_item_marks, s.db_engine, [e.live_authorization_id for e in entries]) \
        if flags.get("single_item") else {}
    usage = policies.usage(rules, entries, entries[-1].ts_sim if entries else s.now(),
                           policies.fulfilment(flags, entries, marked))
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


@router.post("/devices/reset/{card_id}", response_model=api.DeviceReset)
async def reset_devices(card_id: str, request: Request) -> JSONResponse:
    """Remove every device on the card, so the next one enrols as its first: issuer-side
    recovery in a real rollout (docs/passport.md). Refused (403) when ``ONEGUARD_ENV=prod``."""
    if os.environ.get(ENV_VAR, "").strip().lower() == "prod":
        raise ApiError(403, "forbidden", "Resetting a card's devices is operator recovery and is off in production.")
    s = services(request)
    if await s.db(queries.card_customer, s.db_engine, card_id) is None:
        raise not_found(f"No card {card_id}.")
    removed = await s.db(device_store.reset, s.db_engine, card_id, s.now())
    await reissue_passport(s, card_id)
    return reply(api.DeviceReset(card_id=card_id, removed=removed))
