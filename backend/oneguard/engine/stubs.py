"""Trivial implementation of every interface, so every lane can run alone (team-contract §4).

Stubs are deliberately cautious: rules all pass, no signals, every purchase is a
``step_up`` with reason code ``stub``, the explanation says "stub".

``ONEGUARD_STUBS`` picks what runs, read once at import:

- unset or empty: a registered real implementation where one exists, a stub otherwise;
- ``all``: every function stubbed;
- ``none``: no stubs; a missing real implementation is an error at import;
- ``build_facts,decide`` (comma list): those stubbed, the rest as when unset.

``ACTIVE`` maps every interface name to the callable the pipeline uses and
``STUBBED`` names the stubbed ones; both are logged at import.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from oneguard.api.models import DryRunResult
from oneguard.engine.interfaces import INTERFACES, load_implementations
from oneguard.engine.types import (
    CompiledDraft,
    EngineDecision,
    EvidenceRow,
    Explanation,
    Facts,
    FactValue,
    HistoryIndex,
    ItemFacts,
    LedgerView,
    Policy,
    Rule,
    RuleResult,
    Signal,
)
from oneguard.llm.provider import Provider

log = logging.getLogger(__name__)

STUBS_ENV = "ONEGUARD_STUBS"
ZURICH = ZoneInfo("Europe/Zurich")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CENT = Decimal("0.01")


def _chf(amount: float, rate: Decimal) -> float:
    return float((Decimal(str(amount)) * rate).quantize(_CENT, rounding=ROUND_HALF_EVEN))


def _not_extracted(kind: type) -> FactValue:
    return FactValue[kind](value=None, known=False, source="regex", detail="stub: not extracted")


def build_facts(event: dict, history: HistoryIndex) -> Facts:
    """Trusted fields only; nothing is extracted from shop text (all FactValues unknown).

    Unit prices are converted with the event's own rate ``billing_amount_chf / amount``.
    """
    auth = event["authorization"]
    merchant = auth["merchant"]
    timestamp = datetime.fromisoformat(auth["timestamp"])
    local = timestamp.astimezone(ZURICH)
    rate = Decimal(str(auth["billing_amount_chf"])) / Decimal(str(auth["amount"]))
    items = []
    for line in auth["items"]:
        price_range = history.item_price_range(line["item_id"])
        low, typical, high = price_range if price_range else (None, None, None)
        items.append(
            ItemFacts(
                line_no=line["line_no"],
                item_id=line["item_id"],
                item_name=line["item_name"],
                item_category=line["item_category"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                currency=line["currency"],
                unit_price_chf=_chf(line["unit_price"], rate),
                item_details=line["item_details"],
                size_eu=_not_extracted(int),
                return_window_days=_not_extracted(int),
                recurring=_not_extracted(bool),
                unit_price_min_chf=low,
                unit_price_typical_chf=typical,
                unit_price_max_chf=high,
            )
        )
    return Facts(
        authorization_id=auth["authorization_id"],
        source_authorization_id=auth["source_authorization_id"],
        timestamp=timestamp,
        local_weekday=_WEEKDAYS[local.weekday()],
        local_hour=local.hour,
        amount=auth["amount"],
        currency=auth["currency"],
        billing_amount_chf=auth["billing_amount_chf"],
        items=items,
        merchant_id=merchant["merchant_id"],
        merchant_name=merchant["merchant_name"],
        merchant_category=merchant["merchant_category"],
        merchant_country=merchant["merchant_country"],
        merchant_mcc=merchant["merchant_mcc"],
        merchant_recurring_capable=merchant["recurring_capable"] == "true",
        device_id=auth["customer_device_id"],
        recent_attempt_count_10m=auth["recent_attempt_count_10m"],
        order_returnable=auth["order_returnable"],
        order_cancellable=auth["order_cancellable"],
        delivery_by=auth["delivery_by"],
        related_authorization_id=auth["related_authorization_id"],
        related_status=auth["related_authorization_status"],
        return_window_days=_not_extracted(int),
        authority_status=auth["authority_status"],
        card_status_at_attempt=auth["card_status_at_attempt"],
    )


def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]:
    return [
        RuleResult(rule_id=rule.id, outcome="pass", detail="stub", source="event")
        for rule in policy.rules
    ]


def resolve_unknowns(
    facts: Facts, rules: list[RuleResult], provider: Provider, budget_s: float
) -> Facts:
    return facts


def protections(facts: Facts, policy: Policy, ledger: LedgerView) -> list[Signal]:
    return []


def warning_signs(facts: Facts, ledger: LedgerView, policy: Policy) -> list[Signal]:
    return []


def soft_signals(facts: Facts, budget_s: float) -> list[Signal]:
    return []


def decide(
    rules: list[RuleResult],
    protections_: list[Signal],
    warnings_: list[Signal],
    soft: list[Signal],
    policy: Policy,
    ledger: LedgerView,
) -> EngineDecision:
    return EngineDecision(outcome="step_up", reason_codes=["stub"], step=4, deciding_ids=[])


def explain(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    rules: list[RuleResult],
    signals: list[Signal],
) -> Explanation:
    return Explanation(
        message="stub",
        evidence=[EvidenceRow(rule="stub", outcome="info", detail="stub", source="policy")],
    )


def rewrite_explanation(
    explanation: Explanation, facts: Facts, provider: Provider, timeout_s: float
) -> str:
    return explanation.message


def compile_instruction(
    text: str, history: HistoryIndex, card_id: str, provider: Provider
) -> CompiledDraft:
    return CompiledDraft(
        instruction=text,
        rules=[],
        uncertainty_policy="ask",
        open_questions=["stub"],
        dry_run=DryRunResult(sample_size=0, would_violate=0, would_fit=0, would_ask=0, insight="stub"),
        compiler="fallback",
    )


def lint_accepted(rules: list[Rule], accepted_ids: list[str]) -> tuple[list[str], list[str]]:
    # Never a pass-everything stub: C2 still needs a per-order cap and every exact check.
    kept = set(accepted_ids)
    missing: list[str] = []
    reasons: list[str] = []
    if not any(
        r.id in kept and r.field == "authorization.billing_amount_chf" and r.scope != "period"
        for r in rules
    ):
        missing.append("per_order_limit")
        reasons.append("the policy needs a limit on what one purchase may cost")
    for r in rules:
        if r.source == "exact" and r.id not in kept:
            missing.append(r.id)
            reasons.append(f'you stated "{r.text}" and it was left out')
    return missing, reasons


def dry_run(policy: Policy, history: HistoryIndex, card_id: str) -> DryRunResult:
    return DryRunResult(sample_size=0, would_violate=0, would_fit=0, would_ask=0, insight="stub")


STUBS: dict[str, Callable[..., Any]] = {
    "build_facts": build_facts,
    "evaluate_rules": evaluate_rules,
    "resolve_unknowns": resolve_unknowns,
    "protections": protections,
    "warning_signs": warning_signs,
    "soft_signals": soft_signals,
    "decide": decide,
    "explain": explain,
    "rewrite_explanation": rewrite_explanation,
    "compile_instruction": compile_instruction,
    "lint_accepted": lint_accepted,
    "dry_run": dry_run,
}


def select_implementations(
    setting: str | None, real: dict[str, Callable[..., Any]]
) -> tuple[dict[str, Callable[..., Any]], frozenset[str]]:
    """Resolve every interface name for an ``ONEGUARD_STUBS`` value (module docstring)."""
    value = (setting or "").strip().lower()
    if value == "all":
        forced = set(INTERFACES)
    elif value in ("", "none"):
        forced = set()
    else:
        forced = {name.strip() for name in value.split(",") if name.strip()}
        unknown = forced - set(INTERFACES)
        if unknown:
            raise ValueError(f"{STUBS_ENV} names unknown functions: {sorted(unknown)}")
    missing = set(INTERFACES) - set(real) - forced
    if value == "none" and missing:
        raise RuntimeError(f"{STUBS_ENV}=none but no implementation for: {sorted(missing)}")
    stubbed = frozenset(forced | missing)
    active = {name: STUBS[name] if name in stubbed else real[name] for name in INTERFACES}
    return active, stubbed


ACTIVE, STUBBED = select_implementations(os.environ.get(STUBS_ENV), load_implementations())
(log.warning if STUBBED else log.info)(
    "engine functions stubbed (%s=%r): %s",
    STUBS_ENV,
    os.environ.get(STUBS_ENV, ""),
    ", ".join(sorted(STUBBED)) or "none",
)
