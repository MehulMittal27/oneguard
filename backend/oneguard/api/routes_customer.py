"""Customer-facing endpoints C1–C12 (docs/api-contract.md §1.1, §3).

Envelopes are exactly §1.0: C12, C10, C6, C3 wrapped in a named key; C1, C2, C4 the
object itself; C5, C8 ``204`` with no body. Errors are the §3.8 envelope
(``api/errors.py``). Responses are serialised by the contract models, so optional
fields are omitted or explicit ``null`` exactly as ``api/models.py`` says.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from oneguard.api import models as api
from oneguard.api import policies, queries
from oneguard.api.errors import ApiError, not_found
from oneguard.api.services import Services
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import CompiledDraft, HistoryIndex, LedgerView, Policy, Rule
from oneguard.pipeline import to_api_decision
from oneguard.store.schema import Mandate, PolicyDraft
from oneguard.viseca.client import VisecaError
from oneguard.viseca.demo import rule_to_viseca

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def services(request: Request) -> Services:
    return request.app.state.services


def reply(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


# C12, C10 -------------------------------------------------------------------------------


@router.get("/customers", response_model=api.CustomersResponse)
async def list_customers(request: Request) -> JSONResponse:
    """C12. ``scenario_ids``: every scenario bound to the customer's cards (the local pack's
    and the ones the platform named, ``Services.bindings``). ``live``: one of them is served
    now (the worker's ``served_scenarios``); a store that never reached the platform counts
    every bound scenario, so the offline replay still has its customers. ``card_id``: the
    card of their newest live scenario, else newest scenario, else of an active policy."""
    s = services(request)
    rows = await s.db(queries.customers, s.db_engine)
    active = await s.db(queries.mandates, s.db_engine, "active")
    bound = await s.bindings()
    served = await s.db(queries.served_scenarios, s.db_engine)
    policy_card = {m.customer_id: m.card_id for m in active}
    customers = []
    for row in rows:
        bindings = bound.get(row.customer_id, [])
        live = [b for b in bindings if served is None or b.scenario_id in served]
        newest = max(live or bindings, key=lambda b: b.scenario_id, default=None)
        customers.append(
            api.Customer(
                customer_id=row.customer_id,
                name=row.persona_name,
                home_region=row.home_region,
                card_id=newest.card_id if newest else policy_card.get(row.customer_id),
                scenario_ids=[b.scenario_id for b in bindings],
                live=bool(live),
            )
        )
    return reply(api.CustomersResponse(customers=customers))


async def _require_customer(s: Services, customer_id: str) -> None:
    if not await s.db(queries.customer_exists, s.db_engine, customer_id):
        raise not_found(f"No customer {customer_id}.")


@router.get("/customers/{customer_id}/accounts", response_model=api.AccountsResponse)
async def list_accounts(customer_id: str, request: Request) -> JSONResponse:
    """C10. Bank limits are context only, never on the meter."""
    s = services(request)
    await _require_customer(s, customer_id)
    rows = await s.db(queries.accounts, s.db_engine, customer_id)
    return reply(
        api.AccountsResponse(
            accounts=[
                api.Account(
                    account_id=a.account_id,
                    customer_id=a.customer_id,
                    account_type=a.account_type,
                    account_purpose=a.account_purpose,
                    status=a.status,
                    per_transaction_limit_chf=queries.money(a.per_transaction_limit_chf),
                    monthly_limit_chf=queries.money(a.monthly_limit_chf),
                    cards=[
                        api.Card(card_id=c.card_id, card_type=c.card_type, card_purpose=c.card_purpose, status=c.status)
                        for c in cards
                    ],
                )
                for a, cards in rows
            ]
        )
    )


# C6 -------------------------------------------------------------------------------------


def merchant_view(entry: LedgerEntry, earlier: list[LedgerEntry], history: HistoryIndex) -> LedgerView:
    """The ledger's merchant familiarity just before ``entry`` (``Decision.merchant_meta``).

    Counted as ``Ledger.view`` counts it: history on this card and on the customer's other
    cards, plus this run's approvals before the purchase, all joined on ``merchant_id``.
    """
    hist_customer = dict(history.known_merchants(entry.customer_id))
    on_card = dict(history.known_merchants_on_card(entry.card_id))
    other = {m: n - on_card.get(m, 0) for m, n in hist_customer.items() if n > on_card.get(m, 0)}
    for prior in earlier:
        if prior.ts_sim < entry.ts_sim and prior.spent_chf > 0:
            counts = on_card if prior.card_id == entry.card_id else other
            counts[prior.merchant_id] = counts.get(prior.merchant_id, 0) + 1
    return LedgerView(
        period_spent_chf=0.0,
        period_reserved_chf=0.0,
        period_window_start=entry.ts_sim,
        priors=[],
        known_merchant_ids=set(on_card) | set(other),
        known_merchant_ids_on_card=set(on_card),
        merchant_approvals_on_card=on_card,
        merchant_approvals_other_cards=other,
        known_device_ids=set(),
        known_countries=set(),
        max_approved_chf=None,
        flagged_merchant_ids=set(),
        frozen=False,
    )


def mandate_policy(row: Mandate) -> Policy:
    rules, flags = policies.load_rules(row.rules, row.checks)
    return policies.policy_of(row.mandate_id, row.status, row.instruction, rules, flags, row.uncertainty_policy)


def decided_by_platform(event: dict, entry: LedgerEntry) -> Policy:
    """The policy a decision with no stored mandate was checked against: the platform
    mandate's rules from its stored event (as the worker did, ``policy_from_snapshot``),
    else a policy without rules (a replay whose policy was never stored)."""
    from oneguard.viseca.worker import policy_from_snapshot

    mandate = event.get("mandate") or {}
    if mandate.get("mandate_id") == entry.mandate_id and mandate.get("hard_rules"):
        return policy_from_snapshot(mandate)
    return Policy(mandate_id=entry.mandate_id, status="active", instruction="", rules=[], uncertainty_policy="ask")


def build_decisions(
    stored: list[queries.StoredDecision], history: HistoryIndex, mandates: dict[str, Mandate]
) -> list[api.Decision]:
    """Contract ``Decision`` rows in the order given; one without its event is left out.

    Each is mapped with the policy it was decided under (``Decision.confirmable``); a
    replay's policy that was never stored as a mandate maps as one without rules.
    """
    decided_under = {mandate_id: mandate_policy(row) for mandate_id, row in mandates.items()}
    by_run: dict[str, list[LedgerEntry]] = defaultdict(list)
    for item in stored:
        by_run[item.entry.run_id].append(item.entry)
    decisions = []
    for item in stored:
        if item.event is None:
            log.error("decision %s has no stored event; left out of C6", item.entry.live_authorization_id)
            continue
        view = merchant_view(item.entry, by_run[item.entry.run_id], history)
        policy = decided_under.get(item.entry.mandate_id) or decided_by_platform(item.event, item.entry)
        decisions.append(to_api_decision(item.event, item.entry, view, policy, item.run_started_at))
    return decisions


@router.get("/customers/{customer_id}/decisions", response_model=api.DecisionsResponse)
async def list_decisions(customer_id: str, request: Request) -> JSONResponse:
    """C6: the customer's full history, newest first; lapsed step-ups are closed first."""
    s = services(request)
    await _require_customer(s, customer_id)
    stored = await s.db(queries.customer_decisions, s.db_engine, customer_id)
    if await s.close_lapsed(stored):
        stored = await s.db(queries.customer_decisions, s.db_engine, customer_id)
    mandates = await s.db(queries.mandates_by_id, s.db_engine, {d.entry.mandate_id for d in stored})
    return reply(api.DecisionsResponse(decisions=build_decisions(stored, s.history, mandates)))


# C1 -------------------------------------------------------------------------------------


def _unique_ids(rules: list[Rule]) -> list[Rule]:
    seen: dict[str, int] = {}
    out = []
    for rule in rules:
        n = seen[rule.id] = seen.get(rule.id, 0) + 1
        out.append(rule if n == 1 else rule.model_copy(update={"id": f"{rule.id}-{n}"}))
    return out


async def _card_customer(s: Services, card_id: str) -> str:
    customer_id = await s.db(queries.card_customer, s.db_engine, card_id)
    if customer_id is None:
        raise not_found(f"No card {card_id}.")
    return customer_id


@router.post("/cards/{card_id}/policy-drafts", response_model=api.PolicyDraft)
async def create_draft(card_id: str, body: api.PolicyDraftRequest, request: Request) -> JSONResponse:
    """C1: an instruction through the compiler, or a form with rules built directly."""
    s = services(request)
    customer_id = await _card_customer(s, card_id)
    if body.form is not None:
        rules, flags = policies.form_rules(body.form)
        uncertainty = body.form.uncertainty_policy
        instruction = policies.FORM_INSTRUCTION
        open_questions: list[str] = []
        dry_run = policies.form_dry_run(rules, flags, s.history, card_id, customer_id)
        compiler = "form"
    else:
        instruction = body.instruction or ""
        if not instruction.strip():
            raise ApiError(422, "validation", "The instruction is empty.")
        compiled = await _compile(s, instruction, card_id, customer_id)
        rules, flags = _unique_ids(compiled.rules), policies.flags_of(compiled)
        uncertainty = compiled.uncertainty_policy
        open_questions = list(compiled.open_questions)
        dry_run = compiled.dry_run
        compiler = compiled.compiler
    checks = policies.policy_checks(rules, flags)
    if not checks:
        open_questions = [policies.NO_CHECKS_QUESTION, *(q for q in open_questions if q != policies.NO_CAP_QUESTION)]
    elif policies.per_order_cap(rules) is None and policies.NO_CAP_QUESTION not in open_questions:
        open_questions.append(policies.NO_CAP_QUESTION)

    draft = api.PolicyDraft(
        draft_id=f"pd_{secrets.token_hex(8)}",
        card_id=card_id,
        instruction=instruction,
        checks=checks,
        uncertainty_policy=uncertainty,
        open_questions=open_questions,
        dry_run=dry_run,
        compiler=compiler,
    )
    row = PolicyDraft(
        draft_id=draft.draft_id,
        card_id=card_id,
        customer_id=customer_id,
        instruction=instruction,
        rules=policies.store_rules(rules, flags),
        checks=[c.model_dump(mode="json") for c in checks],
        uncertainty_policy=uncertainty,
        open_questions=open_questions,
        dry_run=dry_run.model_dump(mode="json"),
        compiler=compiler,
        viseca_draft_id=None,
        created_at=s.now(),
        confirmed_at=None,
    )
    await s.db(queries.add, s.db_engine, row)
    return reply(draft)


async def _compile(s: Services, instruction: str, card_id: str, customer_id: str) -> CompiledDraft:
    compile_instruction = s.functions["compile_instruction"]
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(compile_instruction, instruction, s.history, card_id, s.provider, customer_id=customer_id),
            s.compile_timeout_s,
        )
    except TimeoutError:
        raise ApiError(
            504, "compiler_timeout", "Reading the instruction took too long. Try again, or use the form."
        ) from None


# C2 -------------------------------------------------------------------------------------


def _mandate(row: Mandate, usage: api.MandateUsage | None) -> api.Mandate:
    return api.Mandate(
        mandate_id=row.mandate_id,
        card_id=row.card_id,
        instruction=row.instruction,
        checks=[api.RuleCheck.model_validate(c) for c in row.checks],
        uncertainty_policy=row.uncertainty_policy,
        open_questions=list(row.open_questions),
        status="active" if row.status == "active" else "revoked",
        confirmed_at=row.confirmed_at,
        usage=usage,
    )


async def _with_usage(s: Services, row: Mandate) -> api.Mandate:
    rules, _ = policies.load_rules(row.rules, row.checks)
    entries = await s.db(queries.latest_run_decisions, s.db_engine, card_id=row.card_id, mandate_id=row.mandate_id)
    return _mandate(row, policies.usage(rules, entries, row.confirmed_at))


@router.post("/policy-drafts/{draft_id}/confirm", response_model=api.Mandate)
async def confirm_draft(draft_id: str, body: api.ConfirmDraftRequest, request: Request) -> JSONResponse:
    """C2: the checks sent back are accepted ids; their text is ignored.

    A draft with no checks at all is refused (409 ``lint_failed``) before anything else.
    The accepted subset is re-linted (a per-purchase cap, no dropped ``exact`` check),
    then created and confirmed at Viseca with the instruction verbatim (a form draft sends
    its accepted checks as sentences instead: the platform wants text), then stored.
    A new policy replaces the card's active one, which is revoked.
    """
    s = services(request)
    async with s.policy_lock:
        row = await s.db(queries.draft, s.db_engine, draft_id)
        if row is None:
            raise not_found(f"No policy draft {draft_id}.")
        if row.confirmed_at is not None:
            raise ApiError(409, "draft_confirmed", "This draft is already confirmed.")
        if not row.checks:
            raise ApiError(
                409, "lint_failed", f"Not confirmed: {policies.NO_CHECKS_REASON}.", {"missing": ["per_order_limit"]}
            )
        draft_rules, flags = policies.load_rules(row.rules, row.checks)
        by_id = {r.id: r for r in draft_rules}
        shown = policies.flag_checks(flags)
        chosen = [c.id for c in body.checks]
        unknown = [i for i in chosen if i not in by_id and i not in {c.id for c in shown}]
        if unknown:
            raise ApiError(422, "validation", "Some checks are not part of this draft.", {"unknown": unknown})
        accepted = [r for r in draft_rules if r.id in set(chosen)]
        missing, reasons = s.functions["lint_accepted"](draft_rules, [r.id for r in accepted])
        # A flag check has no typed rule for lint to see; it is exact, so it may not be dropped either.
        dropped = [c for c in shown if c.id not in set(chosen)]
        missing = [*missing, *(c.id for c in dropped)]
        reasons = [*reasons, *(f'you stated "{c.text}" and it was left out' for c in dropped)]
        if missing:
            raise ApiError(409, "lint_failed", "Not confirmed: " + "; ".join(reasons) + ".", {"missing": missing})
        flags = policies.accepted_flags(flags, draft_rules, accepted)
        uncertainty = body.uncertainty_policy
        checks = policies.policy_checks(accepted, flags)

        viseca_draft_id = viseca_mandate_id = None
        if s.client is not None:
            created = await s.viseca(
                s.client.create_mandate(
                    policies.form_instruction(accepted, uncertainty) if row.compiler == "form" else row.instruction,
                    [rule_to_viseca(r) for r in accepted],
                    uncertainty,
                    guidance=[r.text for r in accepted],
                    open_questions=list(row.open_questions),
                ),
                "new policy",
            )
            viseca_draft_id = str(created["draft_id"])
            confirmed = await s.viseca(s.client.confirm_mandate(viseca_draft_id), "policy confirmation")
            viseca_mandate_id = str(confirmed["mandate_id"])

        now = s.now()
        mandate = Mandate(
            mandate_id=f"md_{secrets.token_hex(8)}",
            viseca_mandate_id=viseca_mandate_id,
            card_id=row.card_id,
            customer_id=row.customer_id,
            instruction=row.instruction,
            rules=policies.store_rules(accepted, flags),
            checks=[c.model_dump(mode="json") for c in checks],
            uncertainty_policy=uncertainty,
            open_questions=list(row.open_questions),
            status="active",
            confirmed_at=now,
            revoked_at=None,
        )
        replaced = await s.db(queries.confirm_draft, s.db_engine, draft_id, mandate, viseca_draft_id, now)
        for old in replaced:
            s.bind_mandate(old)
            await revoke_at_platform(s, old, strict=False)
        s.bind_mandate(mandate)
        return reply(await _with_usage(s, mandate))


# C3, C4, C5 -----------------------------------------------------------------------------


@router.get("/cards/{card_id}/policy", response_model=api.PolicyResponse)
async def get_policy(card_id: str, request: Request) -> JSONResponse:
    """C3: the card's active policy (else its latest, revoked) with usage from the ledger."""
    s = services(request)
    await _card_customer(s, card_id)
    row = await s.db(queries.latest_mandate, s.db_engine, card_id)
    return reply(api.PolicyResponse(mandate=await _with_usage(s, row) if row else None))


@router.post("/cards/{card_id}/policy/tighten", response_model=api.Mandate)
async def tighten_policy(card_id: str, body: api.TightenRequest, request: Request) -> JSONResponse:
    """C4: add checks from this card's drafts and/or move uncertainty to ``decline``.

    ``add_checks`` are ids of checks this card's drafts proposed (their text is ignored).
    Changing a check already in force is not an addition (409); an unknown id is 422.
    """
    s = services(request)
    await _card_customer(s, card_id)
    async with s.policy_lock:
        row = await s.db(queries.latest_mandate, s.db_engine, card_id)
        if row is None or row.status != "active":
            raise not_found("This card has no active policy to tighten.")
        rules, flags = policies.load_rules(row.rules, row.checks)
        in_force = {r.id: r for r in rules}
        proposed = await s.db(queries.card_draft_rules, s.db_engine, card_id)
        added: list[Rule] = []
        unknown: list[str] = []
        for check in body.add_checks:
            if check.id in policies.FLAG_CHECK_IDS and check.id in {c["id"] for c in row.checks}:
                continue  # a flag check already in force: nothing to add
            if check.id not in proposed and check.id not in in_force:
                unknown.append(check.id)
                continue
            rule = Rule.model_validate(proposed[check.id]) if check.id in proposed else in_force[check.id]
            if check.id in in_force:
                if rule != in_force[check.id]:
                    raise ApiError(
                        409, "not_pure_addition", f'"{in_force[check.id].text}" is already in force and cannot be changed.',
                        {"changed": [check.id]},
                    )  # fmt: skip
                continue
            if all(r.id != rule.id for r in added):
                added.append(rule)
        if unknown:
            raise ApiError(422, "validation", "Some checks were never proposed for this card.", {"unknown": unknown})
        uncertainty = body.uncertainty_policy or row.uncertainty_policy
        if not added and uncertainty == row.uncertainty_policy:
            raise ApiError(409, "not_pure_addition", "Nothing to add: every check is already in force.")

        new_rules = [*rules, *added]
        if s.client is not None and row.viseca_mandate_id:
            changes: dict[str, Any] = {
                "hard_rules": [rule_to_viseca(r) for r in new_rules],
                "guidance": [r.text for r in new_rules],
            }
            if uncertainty != row.uncertainty_policy:
                changes["uncertainty_policy"] = uncertainty
            await s.viseca(s.client.patch_mandate(row.viseca_mandate_id, **changes), "tightened policy")
        updated = await s.db(
            queries.update_mandate,
            s.db_engine,
            row.mandate_id,
            rules=policies.store_rules(new_rules, flags),
            checks=[c.model_dump(mode="json") for c in policies.policy_checks(new_rules, flags)],
            uncertainty_policy=uncertainty,
        )
        s.bind_mandate(updated)
        return reply(await _with_usage(s, updated))


async def revoke_at_platform(s: Services, row: Mandate, *, strict: bool) -> None:
    """Revoke at Viseca (the worker flips its policy first, so nothing more is approved).

    A platform that no longer has it active counts as done: 404, or 409 because it is
    already revoked or was superseded (the sandbox keeps one active mandate per team, so
    confirming any policy supersedes the previous one). Its state is read and logged,
    never shown to the customer. With ``strict`` any other failure (5xx, network,
    timeout) is a 503 and nothing is retried; otherwise it is logged.
    """
    tm = row.viseca_mandate_id
    if not tm or s.client is None:
        return
    try:
        if s.worker is not None:
            await s.platform(s.worker.revoke(tm))
        else:
            await s.platform(s.client.delete_mandate(tm))
    except VisecaError as exc:
        if exc.status in (404, 409):
            log.info(
                "Viseca no longer has mandate %s active (%s %s); platform status: %s",
                tm, exc.status, exc.code, await _platform_status(s, tm),
            )  # fmt: skip
            return
        if not strict:
            log.error("could not revoke replaced mandate %s at Viseca: %s", tm, exc)
            return
        raise ApiError(
            503,
            "upstream_unavailable",
            "Revoked in OneGuard, so nothing more is approved under it, but the payment platform "
            "did not confirm. Try again.",
            {"platform_status": exc.status, "platform_code": exc.code},
        ) from None


async def _platform_status(s: Services, tm: str) -> str:
    """The mandate's status at Viseca for the log (``revoked``, ``superseded``, ...)."""
    if s.client is None:
        return "unread (no platform)"
    try:
        mandate = await s.platform(s.client.get_mandate(tm))
        return str(mandate.get("status")) if isinstance(mandate, dict) else "unread (no body)"
    except VisecaError as exc:
        return f"unread ({exc.status or 'network'} {exc.code})"


@router.post("/cards/{card_id}/policy/revoke", status_code=204)
async def revoke_policy(card_id: str, request: Request) -> Response:
    """C5: the policy flips to revoked and is revoked at Viseca; purchases are untouched.

    Revoking an already revoked policy re-confirms it at Viseca (idempotent).
    """
    s = services(request)
    await _card_customer(s, card_id)
    async with s.policy_lock:
        row = await s.db(queries.latest_mandate, s.db_engine, card_id)
        if row is None:
            raise not_found("This card has no policy to revoke.")
        if row.status == "active":
            row = await s.db(
                queries.update_mandate, s.db_engine, row.mandate_id, status="revoked", revoked_at=s.now()
            )
            s.bind_mandate(row)
        await revoke_at_platform(s, row, strict=True)
    return Response(status_code=204)


# C8 -------------------------------------------------------------------------------------


@router.post("/authorizations/{authorization_id}/resolve", status_code=204)
async def resolve_step_up(authorization_id: str, body: api.ResolveRequest, request: Request) -> Response:
    """C8: the customer's answer; 409 when not awaiting one or the window has closed."""
    await services(request).resolve(authorization_id, body.decision)
    return Response(status_code=204)
