"""One event in, one explained decision out (docs/rules.md §4, §4a; api-contract §3.4).

``decide_event`` calls the lane functions in this order:

0. ``Ledger.note_event``: the ledger keeps the purchase's device and shop country (W1, W3)
1. redelivery of a stored live ``authorization_id`` → the stored result, nothing counted (M7)
2. ``build_facts``
3. ``Ledger.view``; ``facts.merchant_known`` / ``merchant_known_on_card`` from it (Q7, C9)
   - a policy that is no longer active (revoked, expired, superseded) is declined here
     with ``card_or_authority_inactive`` without running rules or signals (§4 step 1,
     T6, Q6); this holds whatever the engine functions are, stubs included
4. ``evaluate_rules``
5. ``resolve_unknowns`` only if a rule is unknown and a provider is configured (tier 2),
   then ``evaluate_rules`` again on the new facts; then ``policy.add_ledger_results`` adds
   the results that need the LedgerView (C2 period limits, remembered answers)
6. ``protections``, ``warning_signs``, ``soft_signals`` (only when signals are enabled)
7. ``decide``, then ``explain`` on the rule results and signals with their structured
   counterfactuals (``explain.with_bounds``; decide never reads them)
8. ``Ledger.record`` (and ``flag_merchant`` when A1 triggered, ``mark_requested_item``
   when a single-item mandate's requested item is in the cart, A8), with any
   ``extra_evidence`` (the worker's ``info`` reconciliation rows) appended to the
   explanation's evidence
9. mapped to the API ``Decision``; ``would_approve_if`` and the ``receipt_id`` the
   passport sweep signs the receipt under are stored with the entry (docs/passport.md)

Functions are resolved by name through ``engine/stubs.py`` (``ONEGUARD_STUBS``). Tier 2
and soft signals are optional: when they fail or run out of the internal budget
(``ONEGUARD_ENGINE_BUDGET_MS``, D3) the decision is made without them, which can only
be more cautious (P8). Tier 3 runs after posting and is not called here.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from oneguard import __version__
from oneguard.api import models as api
from oneguard.api import policies
from oneguard.engine import stubs
from oneguard.engine.explain import with_bounds
from oneguard.engine.ledger_base import Ledger, LedgerEntry
from oneguard.engine.policy import (
    COUNT_FIELD,
    add_ledger_results,
    matches_requested_item,
)
from oneguard.engine.types import (
    EngineDecision,
    EvidenceRow,
    Explanation,
    Facts,
    HistoryIndex,
    LedgerView,
    Policy,
    RuleResult,
    Signal,
)
from oneguard.llm.provider import Provider, provider_available
from oneguard.passport.ids import receipt_id_for

log = logging.getLogger(__name__)

BUDGET_ENV = "ONEGUARD_ENGINE_BUDGET_MS"
DEFAULT_BUDGET_MS = 2000
TIER2_MAX_S = 1.5
SIGNAL_BUDGET_ENV = "ONEGUARD_SIGNAL_BUDGET_MS"
DEFAULT_SIGNAL_BUDGET_MS = 500
HUMAN_WINDOW_S = 120

_API_DECISION = {"approve": "approved", "decline": "stopped", "step_up": "uncertain"}
_SESSION_NOTES = {
    "normal": "Nothing unusual about how this purchase was made.",
    "elevated": "Something about how this purchase was made was unusual, so it needs your OK.",
    "frozen": "Several warning signs at once; the next purchase needs your OK before it goes through.",
}


def budget_ms_from_env() -> int:
    raw = os.environ.get(BUDGET_ENV, "").strip()
    return int(raw) if raw else DEFAULT_BUDGET_MS


def signal_budget_s_from_env() -> float:
    """The soft-signal cut-off (``ONEGUARD_SIGNAL_BUDGET_MS``, default 500 ms) in seconds.

    Past it the keyword answer stands (signals.py). A value that is not a positive whole
    number of ms keeps the default: a typo must not turn the model off or wait forever.
    """
    raw = os.environ.get(SIGNAL_BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_SIGNAL_BUDGET_MS / 1000
    try:
        ms = int(raw)
    except ValueError:
        ms = 0
    if ms <= 0:
        log.warning("%s=%r is not a positive number of ms; using %d", SIGNAL_BUDGET_ENV, raw, DEFAULT_SIGNAL_BUDGET_MS)
        ms = DEFAULT_SIGNAL_BUDGET_MS
    return ms / 1000


@dataclass
class PipelineContext:
    """Everything ``decide_event`` needs besides the event.

    ``policy`` is our confirmed mandate for the card; ``ledger`` and ``history`` are the
    run's state. ``human_window_s`` comes from Viseca ``/v1/bootstrap`` (api-contract
    §3.5); the worker may overwrite ``deadline_at`` with the accepted time. ``now`` is
    the real clock (deadlines only, never windows).
    """

    policy: Policy
    ledger: Ledger
    history: HistoryIndex
    run_id: str
    provider: Provider | None = None
    signals_enabled: bool = False
    budget_ms: int = field(default_factory=budget_ms_from_env)
    human_window_s: int = HUMAN_WINDOW_S
    implementations: Mapping[str, Callable[..., Any]] | None = None
    stubbed: frozenset[str] | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def functions(self) -> Mapping[str, Callable[..., Any]]:
        return self.implementations if self.implementations is not None else stubs.ACTIVE

    @property
    def engine_version(self) -> str:
        stubbed = self.stubbed if self.stubbed is not None else (
            frozenset() if self.implementations is not None else stubs.STUBBED
        )
        version = f"oneguard/{__version__} signals={'on' if self.signals_enabled else 'off'}"
        if stubbed:
            names = "all" if stubbed >= set(stubs.STUBS) else ",".join(sorted(stubbed))
            version += f" stubs={names}"
        return version


def period_days_of(policy: Policy, *, spend_only: bool = False) -> int | None:
    """The shortest period window among the policy's period rules, or None (C2).

    ``spend_only`` leaves out purchase counts (``cart.purchases_in_period``): the window
    the platform's ``approved_spend_in_period_chf`` is reconciled over.
    """
    days = [
        r.period_days for r in policy.rules
        if r.scope == "period" and r.period_days and not (spend_only and r.field == COUNT_FIELD)
    ]
    return min(days) if days else None


def _optional_stage(name: str, fn: Callable[[], Any], fallback: Any) -> Any:
    try:
        return fn()
    except Exception:  # an optional tier must never break the decision (D3, P8)
        log.exception("%s failed; deciding without it", name)
        return fallback


def _inactive_policy(policy: Policy) -> tuple[EngineDecision, Explanation]:
    """§4 step 1 for our own mandate: nothing is approved once it is not active (T6, Q6)."""
    reason = "you revoked this policy" if policy.status == "revoked" else f"this policy is {policy.status}"
    return (
        EngineDecision(
            outcome="decline",
            reason_codes=["card_or_authority_inactive"],
            step=1,
            deciding_ids=["policy_status"],
        ),
        Explanation(
            message=f"Declined: {reason}, so nothing is approved under it.",
            counterfactual="Confirm a new policy to let purchases like this go ahead.",
            would_approve_if=[{"requires": "active_policy"}],
            evidence=[
                EvidenceRow(
                    rule="policy_status",
                    outcome="fail",
                    detail=f"Mandate {policy.mandate_id} status is {policy.status}.",
                    source="policy",
                )
            ],
        ),
    )


def decide_event(
    event: dict, ctx: PipelineContext, extra_evidence: Sequence[EvidenceRow] = ()
) -> tuple[EngineDecision, Explanation, api.Decision]:
    """Decide one ``authorization.request`` event (already schema-validated).

    ``extra_evidence`` rows are appended to a new decision's evidence (never to a
    redelivered one); they never change the outcome.
    """
    started = time.perf_counter()
    fn = ctx.functions
    auth = event["authorization"]
    customer_id = event["mandate"]["customer_id"]
    card_id = auth["card_id"]

    ctx.ledger.note_event(event)  # W1, W3 learn this purchase's device and country if approved
    stored = ctx.ledger.get(auth["authorization_id"])
    if stored is not None:
        view = ctx.ledger.view(
            run_id=stored.run_id,
            customer_id=customer_id,
            card_id=card_id,
            at=stored.ts_sim,
            period_days=period_days_of(ctx.policy),
        )
        engine, explanation = from_entry(stored)
        return engine, explanation, to_api_decision(event, stored, view, ctx.policy)

    def remaining_s() -> float:
        return max(0.0, ctx.budget_ms / 1000 - (time.perf_counter() - started))

    facts: Facts = fn["build_facts"](event, ctx.history)
    view: LedgerView = ctx.ledger.view(
        run_id=ctx.run_id,
        customer_id=customer_id,
        card_id=card_id,
        at=facts.timestamp,
        period_days=period_days_of(ctx.policy),
    )
    facts = facts.model_copy(
        update={
            "merchant_known": facts.merchant_id in view.known_merchant_ids,
            "merchant_known_on_card": facts.merchant_id in view.known_merchant_ids_on_card,
        }
    )
    if ctx.policy.status != "active":
        engine, explanation = _inactive_policy(ctx.policy)
        return _record(event, ctx, facts, view, engine, explanation, extra_evidence, started, [])

    rules: list[RuleResult] = fn["evaluate_rules"](facts, ctx.policy)

    if any(r.outcome == "unknown" for r in rules) and provider_available(ctx.provider):
        budget = min(TIER2_MAX_S, remaining_s())
        resolved = _optional_stage(
            "resolve_unknowns",
            lambda: fn["resolve_unknowns"](facts, rules, ctx.provider, budget),
            facts,
        )
        if resolved is not facts:
            facts = resolved
            rules = fn["evaluate_rules"](facts, ctx.policy)

    rules = add_ledger_results(rules, facts, ctx.policy, view)  # C2 + remembered answers (P2)

    protections: list[Signal] = fn["protections"](facts, ctx.policy, view)
    warnings: list[Signal] = fn["warning_signs"](facts, view, ctx.policy)
    soft: list[Signal] = []
    if ctx.signals_enabled:
        budget = min(signal_budget_s_from_env(), remaining_s())
        soft = _optional_stage("soft_signals", lambda: fn["soft_signals"](facts, budget), [])

    engine: EngineDecision = fn["decide"](rules, protections, warnings, soft, ctx.policy, view)
    rules, signals = with_bounds(rules, [*protections, *warnings, *soft], ctx.policy, facts, view)
    explanation: Explanation = fn["explain"](engine, facts, ctx.policy, rules, signals)
    return _record(event, ctx, facts, view, engine, explanation, extra_evidence, started, protections)


def _record(
    event: dict,
    ctx: PipelineContext,
    facts: Facts,
    view: LedgerView,
    engine: EngineDecision,
    explanation: Explanation,
    extra_evidence: Sequence[EvidenceRow],
    started: float,
    protections: list[Signal],
) -> tuple[EngineDecision, Explanation, api.Decision]:
    card_id = event["authorization"]["card_id"]
    customer_id = event["mandate"]["customer_id"]
    latency_ms = round((time.perf_counter() - started) * 1000, 3)

    decided_at = ctx.now()
    pending = engine.outcome == "step_up"
    entry = LedgerEntry(
        live_authorization_id=facts.authorization_id,
        run_id=ctx.run_id,
        mandate_id=ctx.policy.mandate_id,
        card_id=card_id,
        customer_id=customer_id,
        ts_sim=facts.timestamp,
        outcome=engine.outcome,
        final=not pending,
        uncertain_outcome="pending" if pending else None,
        merchant_id=facts.merchant_id,
        item_ids=[item.item_id for item in facts.items],
        billing_amount_chf=facts.billing_amount_chf,
        related_live_id=engine.related[0] if engine.related else None,
        relation=engine.related[1] if engine.related else None,
        session_trust=engine.session_trust,
        step=engine.step,
        deciding_ids=engine.deciding_ids,
        reason_codes=engine.reason_codes,
        evidence=[*explanation.evidence, *extra_evidence],
        message=explanation.message,
        counterfactual=explanation.counterfactual,
        explanation_source=explanation.source,
        injection_flag=explanation.injection_flag,
        engine_version=ctx.engine_version,
        latency_ms=latency_ms,
        signals_enabled=ctx.signals_enabled,
        decided_at=decided_at,
        deadline_at=decided_at + timedelta(seconds=ctx.human_window_s) if pending else None,
        would_approve_if=explanation.would_approve_if,
        receipt_id=receipt_id_for(facts.authorization_id),
    )
    stored = ctx.ledger.record(entry)
    for signal in protections:
        if signal.id == "A1" and signal.triggered:
            ctx.ledger.flag_merchant(ctx.run_id, facts.merchant_id, signal.detail, decided_at)
    item = ctx.policy.requested_item
    if ctx.policy.single_item and item and any(matches_requested_item(line, item) for line in facts.items):
        ctx.ledger.mark_requested_item(facts.authorization_id, item)  # fulfils once a final approval (A8)

    # The stored entry is the truth: under a concurrent redelivery it is the first one.
    engine, explanation = from_entry(stored)
    return engine, explanation, to_api_decision(event, stored, view, ctx.policy)


def from_entry(entry: LedgerEntry) -> tuple[EngineDecision, Explanation]:
    """The engine decision and explanation a stored entry holds (redelivery, tier 3)."""
    related = (entry.related_live_id, entry.relation) if entry.related_live_id and entry.relation else None
    engine = EngineDecision(
        outcome=entry.outcome,
        reason_codes=entry.reason_codes,
        step=entry.step,
        deciding_ids=entry.deciding_ids,
        related=related,
        session_trust=entry.session_trust,
    )
    explanation = Explanation(
        message=entry.message,
        counterfactual=entry.counterfactual,
        evidence=entry.evidence,
        injection_flag=entry.injection_flag,
        source=entry.explanation_source,
        would_approve_if=entry.would_approve_if,
    )
    return engine, explanation


def _uncertainty_note(entry: LedgerEntry) -> str:
    for row in entry.evidence:
        if row.outcome == "uncertain":
            return row.detail
    return entry.message


def confirmable(entry: LedgerEntry, policy: Policy) -> api.Confirmable | None:
    """The one rule no data can check that stepped this purchase up, if that is all that did.

    Only a rule with field ``unverifiable`` qualifies (api-contract.md §3.3); with any
    other deciding rule or signal beside it, a remembered yes would not settle the next ask.
    """
    if entry.outcome != "step_up" or len(entry.deciding_ids) != 1:
        return None
    rule = next((r for r in policy.rules if r.id == entry.deciding_ids[0]), None)
    if rule is None or rule.field != "unverifiable":
        return None
    phrase = rule.value if isinstance(rule.value, str) else rule.text
    return api.Confirmable(rule_id=rule.id, phrase=phrase)


def policy_applied(event: dict, entry: LedgerEntry, policy: Policy) -> api.PolicyApplied | None:
    """The checks this decision was made under, as the customer reads them (the flag checks
    C5 / C10 included, policies.flag_checks). ``platform``
    when the policy is the platform mandate's snapshot (no confirmed policy was bound to it:
    its id is the event's mandate id). None for a policy with no rules (an unknown replay)."""
    if not policy.rules:
        return None
    platform = policy.mandate_id == (event.get("mandate") or {}).get("mandate_id")
    return api.PolicyApplied(
        mandate_id=entry.mandate_id,
        source="platform" if platform else "confirmed",
        checks=policies.policy_checks(policy.rules, policies.flags_of(policy)),
    )


def to_api_decision(
    event: dict,
    entry: LedgerEntry,
    view: LedgerView,
    policy: Policy,
    run_started_at: datetime | None = None,
) -> api.Decision:
    """The contract's ``Decision`` for a stored entry and the event it decided.

    ``run_started_at`` is the real-clock start of the entry's run, when the caller has read
    its ``runs`` row (C6 does).
    """
    auth = event["authorization"]
    merchant = auth["merchant"]
    step_up = entry.outcome == "step_up"
    return api.Decision(
        authorization_id=entry.live_authorization_id,
        customer_id=entry.customer_id,
        card_id=entry.card_id,
        decision=_API_DECISION[entry.outcome],
        uncertain_outcome=entry.uncertain_outcome if step_up else None,
        status="pending_human" if entry.uncertain_outcome == "pending" else "final",
        reason_codes=entry.reason_codes,
        message=entry.message,
        uncertainty=api.Uncertainty(note=_uncertainty_note(entry)) if step_up else None,
        occurred_at=entry.ts_sim,
        merchant=api.DecisionMerchant(
            merchant_id=merchant["merchant_id"], name=merchant["merchant_name"]
        ),
        amount=auth["amount"],
        currency=auth["currency"],
        billing_amount_chf=auth["billing_amount_chf"],
        items=[
            api.DecisionItem(
                item_name=line["item_name"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                currency=line["currency"],
                item_details=line["item_details"],
            )
            for line in auth["items"]
        ],
        injection_flag=api.InjectionFlag.model_validate(entry.injection_flag)
        if entry.injection_flag
        else None,
        evidence=[api.Evidence.model_validate(row.model_dump()) for row in entry.evidence],
        order_returnable=auth["order_returnable"],
        delivery_by=auth["delivery_by"],
        deadline_at=entry.deadline_at if entry.uncertain_outcome == "pending" else None,
        counterfactual=entry.counterfactual,
        related=api.Related(authorization_id=entry.related_live_id, relation=entry.relation)
        if entry.related_live_id and entry.relation
        else None,
        session=api.Session(trust=entry.session_trust, note=_SESSION_NOTES[entry.session_trust]),
        merchant_meta=api.MerchantMeta(
            category=merchant["merchant_category"],
            country=merchant["merchant_country"],
            familiar=entry.merchant_id in view.known_merchant_ids,
            prior_approvals_on_card=view.merchant_approvals_on_card.get(entry.merchant_id, 0),
            prior_approvals_other_cards=view.merchant_approvals_other_cards.get(entry.merchant_id, 0),
        ),
        engine_version=entry.engine_version,
        latency_ms=entry.latency_ms,
        explanation_source=entry.explanation_source,
        resolved_by=entry.resolved_by,
        confirmable=confirmable(entry, policy),
        policy_applied=policy_applied(event, entry, policy),
        run_id=entry.run_id,
        run_started_at=run_started_at,
        would_approve_if=entry.would_approve_if,
        receipt_id=entry.receipt_id,
    )
