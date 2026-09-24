"""Function signatures each lane implements (frozen interface file, team-contract §3.1).

A lane implements a function in its own module and registers it::

    from oneguard.engine.interfaces import register

    @register("build_facts")
    def build_facts(event: dict, history: HistoryIndex) -> Facts: ...

``pipeline.py`` resolves every function by name through ``engine/stubs.py``, which
imports the modules in ``IMPLEMENTATION_MODULES`` (so their ``@register`` runs) and
fills the gaps with stubs according to ``ONEGUARD_STUBS``. The bodies below only
document the contract; calling them raises NotImplementedError.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from oneguard.engine.types import (
    CompiledDraft,
    EngineDecision,
    Explanation,
    Facts,
    HistoryIndex,
    LedgerView,
    Policy,
    RuleResult,
    Signal,
)
from oneguard.llm.provider import Provider

F = TypeVar("F", bound=Callable[..., Any])


def build_facts(event: dict, history: HistoryIndex) -> Facts:
    """P2 facts.py. Trusted event fields + allowlisted regex (M1–M3, A2, C6, C7)."""
    raise NotImplementedError


def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]:
    """P2 policy.py. One RuleResult per customer rule (C1–C12) plus STEP1_RULE_IDS."""
    raise NotImplementedError


def resolve_unknowns(
    facts: Facts, rules: list[RuleResult], provider: Provider, budget_s: float
) -> Facts:
    """P4 tier2.py. Tier 2 (§4a): fill unknown facts from shop text, source "model"."""
    raise NotImplementedError


def protections(facts: Facts, policy: Policy, ledger: LedgerView) -> list[Signal]:
    """P5 protections.py. A1–A7 (§7)."""
    raise NotImplementedError


def warning_signs(facts: Facts, ledger: LedgerView, policy: Policy) -> list[Signal]:
    """P5 warnings.py. W1–W6 (§8); ``policy`` gives W4 the stated per-order limit (C1)."""
    raise NotImplementedError


def soft_signals(facts: Facts, budget_s: float) -> list[Signal]:
    """P5 signals.py. S_agent_directed (keywords / Laya); only adds friction (P5)."""
    raise NotImplementedError


def decide(
    rules: list[RuleResult],
    protections_: list[Signal],
    warnings_: list[Signal],
    soft: list[Signal],
    policy: Policy,
    ledger: LedgerView,
) -> EngineDecision:
    """P2 decide.py. The step order of §4 with D1–D3 and M5."""
    raise NotImplementedError


def explain(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    rules: list[RuleResult],
    signals: list[Signal],
) -> Explanation:
    """P5 explain.py. Template message, evidence, counterfactual (E1–E7)."""
    raise NotImplementedError


def rewrite_explanation(
    explanation: Explanation, facts: Facts, provider: Provider, timeout_s: float,
    *, instruction: str | None = None,
) -> str:
    """P4 tier3.py. Tier 3 (§4a, E8): rewrite from structured evidence, after posting.

    ``instruction`` is the customer's instruction, for the language only. The worker
    calls it in the background once the decision is posted and stores a changed message
    with ``explanation_source="model"``."""
    raise NotImplementedError


def compile_instruction(
    text: str, history: HistoryIndex, card_id: str, provider: Provider,
    *, confirmed_at: datetime | None = None,
) -> CompiledDraft:
    """P4 compiler/. Instruction → typed rules + dry-run (§10, api-contract §3.2).

    ``confirmed_at`` is when the customer confirmed the instruction (C2): relative dates
    ("by Friday") count from its Europe/Zurich date; without it, from the card's
    simulated present (its latest history row, M6)."""
    raise NotImplementedError


INTERFACES: dict[str, Callable[..., Any]] = {
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
}
"""Every interface by name: the contract documentation above."""

IMPLEMENTATION_MODULES: dict[str, str] = {
    "build_facts": "oneguard.engine.facts",
    "evaluate_rules": "oneguard.engine.policy",
    "resolve_unknowns": "oneguard.engine.tier2",
    "protections": "oneguard.engine.protections",
    "warning_signs": "oneguard.engine.warnings",
    "soft_signals": "oneguard.engine.signals",
    "decide": "oneguard.engine.decide",
    "explain": "oneguard.engine.explain",
    "rewrite_explanation": "oneguard.engine.tier3",
    "compile_instruction": "oneguard.compiler",
}
"""Where each lane's real implementation lives (docs/team-contract.md §1)."""

IMPLEMENTATIONS: dict[str, Callable[..., Any]] = {}
"""Real implementations registered by lane modules, by interface name."""


def register(name: str) -> Callable[[F], F]:
    """Register the decorated function as the real implementation of ``name``."""
    if name not in INTERFACES:
        raise ValueError(f"unknown interface {name!r}; expected one of {sorted(INTERFACES)}")

    def decorator(fn: F) -> F:
        IMPLEMENTATIONS[name] = fn
        return fn

    return decorator


def load_implementations() -> dict[str, Callable[..., Any]]:
    """Import every lane module that exists so its ``@register`` runs.

    A module that does not exist yet is skipped; an import error inside a module that
    does exist propagates.
    """
    for module in dict.fromkeys(IMPLEMENTATION_MODULES.values()):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    return dict(IMPLEMENTATIONS)
