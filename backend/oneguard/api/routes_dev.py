"""Operator endpoints D1–D8 (docs/api-contract.md §1.2). Never called by the UI.

This module and ``replay/`` are the only places that know about scenarios (CLAUDE.md
rule 3): which customer and card a scenario runs on, and its events. D1/D2 replay the
data pack offline through the same engine and ledger as live runs; D3/D4 start and
follow a Viseca run; D5 switches the models off or on; D6 shows the ledger itself.

D2 replays the local pack's purchases for a pack scenario; any other catalogued scenario
it replays from record (the stored events of its newest live run, never a platform call),
or refuses as not run yet. D3 accepts any scenario in the
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
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from oneguard.api import models as api
from oneguard.api import policies, queries
from oneguard.api.errors import (
    ApiError,
    not_found,
    platform_detail,
    upstream_unavailable,
)
from oneguard.api.offline import RecordRun, live_ids, record_fields
from oneguard.api.operator import require_operator
from oneguard.api.routes_customer import (
    new_draft,
    register_at_platform,
    register_draft,
    reissue_passport,
    reply,
    revoke_at_platform,
    services,
    store_confirmed,
)
from oneguard.api.services import ScenarioBinding, Services
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import CompiledDraft, Policy
from oneguard.passport import devices as device_store
from oneguard.replay.events import Pack, build_events, recorded_events
from oneguard.store import worker_events
from oneguard.store.db import session
from oneguard.store.schema import Mandate, Run, ScenarioCatalogue
from oneguard.store.seed import ENV_VAR
from oneguard.viseca.client import RUNS_DISABLED_MESSAGE, VisecaError, runs_allowed
from oneguard.viseca.worker import (
    PLATFORM_PENDING,
    first_value,
    run_finished,
    stored_platform_mandate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dev", dependencies=[Depends(require_operator)])
"""Every route here is behind the operator gate (``operator.require_operator``) in production."""
catalogue_router = APIRouter(prefix="/api")
"""D9 (``GET /api/scenarios``): operator data outside ``/api/dev``, kept in this module."""

REPLAY_MANDATE = "TM_REPLAY"
"""The platform mandate id replay events carry when the policy has none at Viseca."""

ReplaySource = Literal["card", "revoked", "scenario"]


def compiled_mandate_id(scenario_id: str) -> str:
    """The mandate id of a scenario's instruction compiled for a replay (D2, never stored)."""
    return f"replay-{scenario_id}"


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
    s = services(request)
    status = s.offline.status()
    if status is None:
        raise not_found("No replay has run yet.")
    return reply(await _replay_named(s, status))


async def _replay_named(s: Services, status: api.ReplayStatus) -> api.ReplayStatus:
    """``status`` with the holder of its card, when the store knows the card."""
    customer_id = status.customer_id or await s.db(queries.card_customer, s.db_engine, status.card_id)
    if customer_id is None:
        return status
    names = await s.db(queries.customer_names, s.db_engine, [customer_id])
    return status.model_copy(update={"customer_id": customer_id, "customer_name": names.get(customer_id)})


async def _instruction(s: Services, scenario_id: str) -> str:
    """The scenario's cardholder instruction: the pack's, else the served catalogue's."""
    if scenario_id in pack().scenarios:
        return pack().scenarios[scenario_id]["cardholder_instruction"]
    row = await s.db(_catalogue_row, s.db_engine, scenario_id)
    if row is None:
        raise not_found(f"No scenario {scenario_id}.")
    return row.cardholder_instruction


def _catalogue_row(db: Engine, scenario_id: str) -> ScenarioCatalogue | None:
    with session(db) as s:
        return s.get(ScenarioCatalogue, scenario_id)


async def _replay_policy(s: Services, scenario_id: str, card_id: str) -> tuple[Policy, str, ReplaySource]:
    """The card's own policy (the customer's words and checks): its active one, else the
    one it had last, revoked, under which every purchase declines at step 1. Only a card
    that never had a policy replays the scenario's instruction, compiled for this replay
    only (never stored as a mandate). Returns the policy, the platform mandate id and where
    the policy came from."""
    row = await s.db(queries.latest_mandate, s.db_engine, card_id)
    if row is not None:
        return policies.mandate_policy(row), row.viseca_mandate_id or REPLAY_MANDATE, "card" if row.status == "active" else "revoked"
    instruction = await _instruction(s, scenario_id)
    compile_instruction = s.functions["compile_instruction"]
    try:
        draft: CompiledDraft = await asyncio.wait_for(
            asyncio.to_thread(compile_instruction, instruction, s.history, card_id, s.provider),
            s.compile_timeout_s,
        )
    except TimeoutError:
        raise ApiError(504, "compiler_timeout", "Compiling the scenario's instruction took too long.") from None
    policy = Policy(
        mandate_id=compiled_mandate_id(scenario_id),
        status="active",
        instruction=draft.instruction,
        rules=draft.rules,
        uncertainty_policy=draft.uncertainty_policy,
        **policies.flags_of(draft),
    )
    return policy, REPLAY_MANDATE, "scenario"


async def _record(s: Services, scenario_id: str, card_id: str) -> tuple[str, RecordRun]:
    """D2 for a scenario the pack has no purchases for: (customer id, the live run to
    replay from record). 404 unknown scenario or not run yet, 422 another card."""
    if not await s.db(_catalogued, s.db_engine, scenario_id):
        raise not_found(f"No scenario {scenario_id}.")
    row = await s.db(queries.record_run, s.db_engine, scenario_id)
    if row is None:
        raise not_found(f"Scenario {scenario_id} has not run yet: no stored events to replay.")
    if row.card_id != card_id:
        raise ApiError(422, "validation", f"Scenario {scenario_id} ran on card {row.card_id}, not {card_id}.")
    customer_id = await s.db(queries.card_customer, s.db_engine, card_id)
    if customer_id is None:
        raise not_found(f"No card {card_id}.")
    return customer_id, RecordRun(row.run_id, row.viseca_run_id, row.started_at)


@router.post("/replay/restart", response_model=api.ReplayStatus)
async def replay_restart(body: api.ReplayRestartRequest, request: Request) -> JSONResponse:
    """D2: replay a scenario's purchases offline, ``speed_ms`` apart.

    A scenario of the local pack replays the pack's purchases. Any other catalogued
    scenario replays from record: the stored events (``events_raw``) of its newest live
    run, verbatim but for fresh ids, through the current engine and policy. Nothing is
    fetched from or posted to the platform: step-ups are answered on the phone (C8,
    closed locally) or expire (Q2)."""
    s = services(request)
    record: RecordRun | None = None
    if body.scenario_id in pack().scenarios:
        customer_id, card_id = _scenario(body.scenario_id, body.card_id)
    else:
        card_id = body.card_id
        customer_id, record = await _record(s, body.scenario_id, card_id)
    policy, platform_mandate, source = await _replay_policy(s, body.scenario_id, card_id)
    if record is None:
        sources = [row["authorization_id"] for row in pack().attempts_for(body.scenario_id)]
        events = build_events(
            pack(), body.scenario_id, mandate_id=platform_mandate, live_id=live_ids(sources).__getitem__
        )
    else:
        stored = await s.db(queries.stored_events, s.db_engine, record.run_id)
        ids = live_ids([e["authorization"]["authorization_id"] for e in stored])
        events = recorded_events(stored, mandate_id=platform_mandate, live_id=ids.__getitem__)
    models = s.replay_models()
    status = await s.offline.restart(
        scenario_id=body.scenario_id,
        card_id=card_id,
        customer_id=customer_id,
        policy=policy,
        policy_source=source,
        events=events,
        history=s.history,
        provider=s.decision_provider(models),
        signals_enabled=models,
        speed_ms=body.speed_ms,
        record=record,
    )
    return reply(await _replay_named(s, status))


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

    Before the run, the policy's mandate is read at the platform (``_platform_mandate``):
    the sandbox keeps one active mandate per team, so a policy confirmed later (another
    card, another judging run) supersedes ours there while it stays active here. Then the
    same policy is registered again at the platform and the run starts under the new
    platform mandate; the reply's ``platform_mandate`` says what was found and done.

    ``policy: "scenario"`` (the operator console) runs the scenario's own instruction
    instead: the platform refuses a run whose mandate's instruction is not the scenario's
    exactly (409 ``instruction_mismatch``), so D3 drafts the served cardholder instruction
    verbatim as C1 does, confirms every check it proposes as C2 does (an operator run: no
    device signature, docs/decisions.md), registers it and starts the run under it; only
    then does it replace the card's active policy, which is ``superseded`` with a note
    naming the run (``_scenario_run``). A platform refusal of the run answers 503 with the
    platform's status, code and message, and nothing was changed.
    """
    if not runs_allowed():
        raise ApiError(409, "runs_disabled", RUNS_DISABLED_MESSAGE)
    s = services(request)
    if not body.force:
        await _refuse_while_running(s, body.scenario_id)
    customer_id = await _live_scenario(s, body.scenario_id, body.card_id)
    row = None
    if body.policy != "scenario":
        row = await s.db(queries.latest_mandate, s.db_engine, body.card_id)
        if row is None or row.status != "active" or not row.viseca_mandate_id:
            raise ApiError(409, "validation", "The card needs an active policy confirmed at Viseca first.")
    if s.worker is None or s.client is None:
        raise ApiError(503, "upstream_unavailable", "The payment platform is not connected.")
    await s.worker.refresh_bootstrap("run start")
    if row is None:
        row, platform, started = await _scenario_run(s, body.scenario_id, body.card_id, customer_id)
    else:
        row, platform = await _platform_mandate(s, row)
        started = await _start_run(s, body.scenario_id, row)
    assert row.viseca_mandate_id is not None
    run_id = str(started["run_id"])
    total = first_value(started, "generated_event_count", "total", "total_events", "event_count")
    s.live_started[run_id] = s.now()
    s.worker.track_run(
        run_id,
        scenario_id=body.scenario_id,
        viseca_mandate_id=row.viseca_mandate_id,
        total=total if isinstance(total, int) and not isinstance(total, bool) else None,
        platform_mandate=platform,
    )
    profiles = [p for p in await s.worker.remember_profiles(started, "run") if p.scenario_id == body.scenario_id]
    card_id = profiles[0].card_id if profiles else body.card_id
    s.worker.track_run(run_id, card_id=card_id)  # D4 and D7 name the card while the run starts
    mandate_id = row.mandate_id
    if card_id != body.card_id and profiles and profiles[0].customer_id:
        mandate_id = await _move_policy(s, row.mandate_id, card_id, profiles[0].customer_id)
    live = s.worker.live_run(run_id)
    assert live is not None
    return reply(
        await _with_customer(s, live.model_copy(update={"card_id": live.card_id or card_id, "mandate_id": mandate_id}))
    )


async def _start_run(s: Services, scenario_id: str, row: Mandate) -> dict[str, Any]:
    """``POST /v1/scenario-runs`` under ``row``'s platform mandate, announced to the worker
    first (``expect_run``). A platform refusal is a 503 ``upstream_unavailable`` whose detail
    carries the platform's ``platform_status``, ``platform_code`` and, when it answered,
    ``platform_message`` verbatim (the console shows it as is)."""
    assert s.worker is not None and s.client is not None and row.viseca_mandate_id is not None
    tm = row.viseca_mandate_id
    s.worker.expect_run(tm, scenario_id)
    try:
        return await s.platform(s.client.create_run(scenario_id, tm))
    except BaseException as exc:
        s.worker.forget_run(tm, scenario_id)
        if isinstance(exc, VisecaError):
            log.warning("Viseca new run failed: %s", exc)
            raise upstream_unavailable(
                "The payment platform did not accept the new run; nothing was changed.", platform_detail(exc)
            ) from None
        raise


async def _scenario_run(
    s: Services, scenario_id: str, card_id: str, customer_id: str
) -> tuple[Mandate, api.PlatformMandate, dict[str, Any]]:
    """D3 ``policy: "scenario"``: the served catalogue's cardholder instruction, verbatim,
    drafted (C1's ``new_draft``) and confirmed with every check it proposes, its uncertainty
    setting and open questions (C2's ``register_draft``, as ``make demo-live`` confirms
    them) at the platform, then the run started under it (``_start_run``).

    Only once the platform has accepted the run is the policy stored as the card's active
    one (``store_confirmed``): the card's earlier policy is ``superseded`` with a note naming
    this run, and the new policy's passport starts with ``operator_run``. Until then the
    worker decides the run's purchases under it from memory (``bind_policy``). A refused
    registration changes nothing (503, as C2); a refused run is undone
    (``_undo_registration``): the new platform mandate is revoked, and the card keeps the
    policy it had (or none), with its passport. The whole of it holds ``s.policy_lock``, so
    no policy change of the customer's comes in between."""
    catalogued = await s.db(_catalogue_row, s.db_engine, scenario_id)
    if catalogued is None:
        raise not_found(f"No scenario {scenario_id}.")
    assert s.worker is not None
    draft = await new_draft(s, card_id, customer_id, instruction=catalogued.cardholder_instruction)
    operator_run = f"the operator's judging run of {scenario_id}"
    async with s.policy_lock:
        stored = await s.db(queries.draft, s.db_engine, draft.draft_id)
        assert stored is not None
        previous = await s.db(queries.latest_mandate, s.db_engine, card_id)
        if previous is not None and previous.status != "active":
            previous = None
        registered = await register_draft(
            s,
            stored,
            [c.id for c in draft.checks],
            draft.uncertainty_policy,
            note=f"Registered for {operator_run}, without the customer's confirmation.",
        )
        new = registered.mandate
        assert new.viseca_mandate_id is not None
        s.worker.bind_policy(new.viseca_mandate_id, policies.mandate_policy(new))
        try:
            started = await _start_run(s, scenario_id, new)
        except Exception:
            await _undo_registration(s, new, scenario_id)
            raise
        mandate = await store_confirmed(s, stored, registered, operator_run=operator_run)
    log.warning(
        "judging run of %s: card %s now runs the scenario's instruction as %s (%s at the platform), replacing %s",
        scenario_id, card_id, mandate.mandate_id, mandate.viseca_mandate_id,
        previous.mandate_id if previous else "no policy",
    )  # fmt: skip
    return (
        mandate,
        api.PlatformMandate(
            status_before=None,
            reregistered=False,
            viseca_mandate_id=new.viseca_mandate_id,
            previous_viseca_mandate_id=previous.viseca_mandate_id if previous else None,
            registered_for_run=True,
            replaced_mandate_id=previous.mandate_id if previous else None,
        ),
        started,
    )


async def _undo_registration(s: Services, new: Mandate, scenario_id: str) -> None:
    """The platform refused the run D3 registered the scenario's policy for: revoke that
    platform mandate (DELETE, best effort; a refusal is kept in ``worker_events`` by the
    client and logged) and re-issue the card's passport. The policy was never stored, so
    the card keeps the policy it had, active, or none."""
    log.warning(
        "judging run of %s refused: the scenario's policy %s (%s at the platform) is revoked there; card %s keeps its policy",
        scenario_id, new.mandate_id, new.viseca_mandate_id, new.card_id,
    )  # fmt: skip
    await revoke_at_platform(s, new, strict=False)
    await reissue_passport(s, new.card_id)


async def _platform_mandate(s: Services, row: Mandate) -> tuple[Mandate, api.PlatformMandate]:
    """D3: the policy's mandate as the platform has it, registered again when not active.

    ``GET /v1/mandates/{id}``: ``active`` starts the run under it. Missing (404), or any
    other status (``superseded`` by a later confirmation, ``revoked`` or ``expired`` at
    the platform while the customer's policy is active here) registers the same policy
    again (``_reregister``). A read that fails otherwise starts the run as before, so the
    platform's own answer to the run decides (a refusal is a 503 naming it)."""
    assert s.client is not None and row.viseca_mandate_id is not None
    tm = row.viseca_mandate_id
    try:
        found = await s.platform(s.client.get_mandate(tm))
    except VisecaError as exc:
        if exc.status != 404:
            log.warning("mandate %s unread at the platform (%s); starting the run under it", tm, exc)
            return row, api.PlatformMandate(status_before=None, reregistered=False, viseca_mandate_id=tm)
        found = {"status": "missing"}
    status = found.get("status") if isinstance(found, dict) else None
    if not isinstance(status, str) or not status:
        log.warning("mandate %s read at the platform with no status; starting the run under it", tm)
        return row, api.PlatformMandate(status_before=None, reregistered=False, viseca_mandate_id=tm)
    if status == "active":
        return row, api.PlatformMandate(status_before=status, reregistered=False, viseca_mandate_id=tm)
    return await _reregister(s, row, status)


async def _reregister(s: Services, row: Mandate, status_before: str) -> tuple[Mandate, api.PlatformMandate]:
    """Create and confirm the card's active policy at the platform again, as C2 did (its
    instruction, a form policy's checks as sentences, its checks as ``hard_rules``), and
    store the new platform id on the same policy: its ``mandate_id``, checks and status
    stay as they are, only the platform reference changes, and the passport is re-issued
    to name it. Tighten only holds: nothing about the policy changes (CLAUDE.md rule 7)."""
    old = row.viseca_mandate_id
    async with s.policy_lock:
        current = await s.db(queries.latest_mandate, s.db_engine, row.card_id)
        if current is None or current.mandate_id != row.mandate_id or current.status != "active":
            raise ApiError(409, "validation", "The card's policy changed while the run was starting; nothing was started.")
        if current.viseca_mandate_id != old:  # registered again meanwhile (another D3)
            assert current.viseca_mandate_id is not None
            return current, api.PlatformMandate(
                status_before=status_before,
                reregistered=True,
                viseca_mandate_id=current.viseca_mandate_id,
                previous_viseca_mandate_id=old,
            )
        rules, _ = policies.load_rules(current.rules, current.checks)
        instruction = current.instruction
        if instruction == policies.FORM_INSTRUCTION:
            instruction = policies.form_instruction(rules, current.uncertainty_policy)
        _, tm = await register_at_platform(
            s, instruction, rules, current.uncertainty_policy, list(current.open_questions), "re-registered policy"
        )
        updated = await s.db(queries.update_mandate, s.db_engine, current.mandate_id, viseca_mandate_id=tm)
        s.bind_mandate(updated)
    log.warning(
        "mandate %s of policy %s was %s at the platform; the policy is registered again as %s",
        old, updated.mandate_id, status_before, tm,
    )  # fmt: skip
    try:
        await s.db(
            worker_events.record,
            s.db_engine,
            worker_events.Event(
                at=s.now(),
                kind="mandate_reregistered",
                action="create_run",
                code=status_before,
                message=f"policy {updated.mandate_id} registered again: {old} was {status_before}",
                mandate_id=tm,
            ),
        )
    except ApiError:  # the policy is registered and stored; the run still starts
        log.exception("re-registration of %s not recorded in worker_events", updated.mandate_id)
    await reissue_passport(s, updated.card_id)
    return updated, api.PlatformMandate(
        status_before=status_before, reregistered=True, viseca_mandate_id=tm, previous_viseca_mandate_id=old
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
    # The passports follow after the reply: D3 answers as fast as before, and the run
    # it started is not held up by signing (the sweep catches up if this fails).
    s.spawn(_reissue_passports(s, [card_id, *source_card]), "passport-reissue")
    return moved.mandate_id


async def _reissue_passports(s: Services, cards: list[str]) -> None:
    for card in dict.fromkeys(cards):
        await reissue_passport(s, card)


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
        ledger_run_id=row.run_id,
        started_at=row.started_at,
        platform_mandate=stored_platform_mandate(row.platform_mandate),
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
        return reply(await _replay_named(s, status))
    stored = await s.db(queries.run_row, s.db_engine, run_id)
    if stored is None:
        raise not_found(f"No run {run_id}.")
    status = api.ReplayStatus(
        scenario_id=stored.scenario_id or "",
        card_id=stored.card_id,
        delivered=stored.delivered,
        total=stored.total,
        running=False,
        next_at=None,
        ledger_run_id=stored.run_id,
        started_at=stored.started_at,
        decided=stored.decided,
        mandate_id=stored.mandate_id,
        policy_source=await _stored_replay_source(s, stored),
        **record_fields(await _stored_record(s, stored.record_run_id)),
    )
    return reply(await _replay_named(s, status))


async def _stored_replay_source(s: Services, row: Run) -> ReplaySource | None:
    """Where a stored replay's policy came from: a stored mandate is the card's, active or
    already revoked when the replay started; D2's compiled id is the scenario's; anything
    else (``make replay``'s fixture) says nothing."""
    if row.scenario_id and row.mandate_id == compiled_mandate_id(row.scenario_id):
        return "scenario"
    mandate = (await s.db(queries.mandates_by_id, s.db_engine, [row.mandate_id])).get(row.mandate_id)
    if mandate is None:
        return None
    revoked = mandate.status != "active" and (mandate.revoked_at is None or mandate.revoked_at <= row.started_at)
    return "revoked" if revoked else "card"


async def _stored_record(s: Services, run_id: str | None) -> RecordRun | None:
    """The live run a stored replay replayed from record, while the store still has it."""
    row = await s.db(queries.run_row, s.db_engine, run_id) if run_id else None
    return RecordRun(row.run_id, row.viseca_run_id, row.started_at) if row is not None else None


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


# D9 -------------------------------------------------------------------------------------


@catalogue_router.get("/scenarios", response_model=api.ScenarioSummariesResponse)
async def scenario_summaries(request: Request) -> JSONResponse:
    """D9: every scenario in the store's catalogue (the pack's and the ones the platform
    served), with the customer and card it runs on (``Services.bindings``: the platform's
    ``scenario_profiles``, else the pack's) and its purchase count. Grouped by customer,
    by name, then by scenario id; scenarios no one has named a card for come last. Reads
    the store only: no platform call, nothing changed."""
    s = services(request)
    catalogue = await s.db(queries.scenario_catalogue, s.db_engine)
    bound = {b.scenario_id: (customer, b.card_id) for customer, bs in (await s.bindings()).items() for b in bs}
    names = await s.db(queries.customer_names, s.db_engine, [c for c, _ in bound.values()])
    recorded = await s.db(queries.recorded_scenarios, s.db_engine)
    rows = []
    for row in catalogue:
        customer_id, card_id = bound.get(row.scenario_id, (None, None))
        rows.append(
            api.ScenarioSummary(
                scenario_id=row.scenario_id,
                name=row.scenario_name,
                event_count=row.event_count,
                instruction=row.cardholder_instruction,
                customer_id=customer_id,
                customer_name=names.get(customer_id, customer_id) if customer_id else None,
                card_id=card_id,
                replay_source=_replay_source(row.scenario_id, recorded),
            )
        )
    rows.sort(key=lambda r: (r.customer_id is None, r.customer_name or "", r.customer_id or "", r.scenario_id))
    return reply(api.ScenarioSummariesResponse(scenarios=rows))


def _replay_source(scenario_id: str, recorded: set[str]) -> str | None:
    """What D2 replays for the scenario (``ScenarioSummary.replay_source``)."""
    if scenario_id in pack().scenarios:
        return "pack"
    return "record" if scenario_id in recorded else None


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
