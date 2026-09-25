"""Policy compiler (P4): instruction -> typed rules -> lint -> dry-run (rules.md §10).

The English parser's reading (parser.py) is the floor. The LLM reading (llm.py) ships
only when it passes lint and keeps every kind of restriction the parser found; its job
is to add what the parser cannot read (other phrasing, other languages), never to
replace what it can. Otherwise the parser's reading ships with ``compiler: fallback``
and a warning in the log. The instruction is kept verbatim. Lint problems that remain
are put to the customer as open questions; nothing is guessed (T2).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import TypeVar

from oneguard.api.models import DryRunResult
from oneguard.compiler.draft import ParsedDraft
from oneguard.compiler.dryrun import dry_run
from oneguard.compiler.lint import LintResult, lint, lint_accepted, lint_against_floor
from oneguard.compiler.llm import read_with_llm
from oneguard.compiler.parser import parse
from oneguard.compiler.resolve import confirmation_date, simulated_today
from oneguard.engine.interfaces import INTERFACES, register
from oneguard.engine.types import CompiledDraft, HistoryIndex, Policy, Rule
from oneguard.llm.provider import Provider, ProviderUnavailable, provider_available

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., object])

_QUESTION_FOR = {
    "no_amount_cap": "No amount stated: what is the most one purchase may cost?",
}


def _with_questions(draft: ParsedDraft, result: LintResult) -> ParsedDraft:
    """Put what lint still finds to the customer rather than hiding it."""
    extra = [_QUESTION_FOR.get(i.code, f"Please check: {i.message}.") for i in result.issues]
    if not extra:
        return draft
    return draft.model_copy(update={"open_questions": list(dict.fromkeys(draft.open_questions + extra))})


@register("compile_instruction")
def compile_instruction(
    text: str, history: HistoryIndex, card_id: str, provider: Provider, customer_id: str | None = None,
    *, confirmed_at: datetime | None = None, today: date | None = None,
    preferences: str | None = None,
) -> CompiledDraft:
    """api-contract §3.2 C1 with ``instruction``. ``customer_id`` is the card's owner, for
    the dry run's customer-level ``agent_history``.

    Relative dates ("by Friday") count from ``today``: the card's simulated present (its
    latest history row, M6), or the Europe/Zurich date of ``confirmed_at`` when a caller
    gives one (no route does: C2 does not pass the real clock). An explicit ``today``
    wins over both."""
    if today is None:
        today = confirmation_date(confirmed_at) if confirmed_at else simulated_today(history, card_id)

    fallback = parse(text, history, card_id, today)
    fallback_lint = lint(fallback)
    chosen, chosen_lint, compiler = fallback, fallback_lint, "fallback"
    if provider_available(provider):
        try:
            read = read_with_llm(text, provider, history, card_id, today, preferences)
            rejected = lint(read, fallback.asked_about).issues + lint_against_floor(read, fallback)
            if not rejected:
                chosen, chosen_lint, compiler = read, LintResult(issues=[]), "llm"
            else:
                log.warning("compiler: LLM reading rejected, using the rule-based reading: %s",
                            "; ".join(f"{i.code}: {i.message}" for i in rejected))
        except ProviderUnavailable as exc:
            log.warning("compiler: LLM unavailable, using fallback: %s", exc)

    chosen = _with_questions(chosen, chosen_lint)
    result: DryRunResult = dry_run(chosen, history, card_id, customer_id)
    return CompiledDraft(
        instruction=text,
        rules=chosen.rules,
        uncertainty_policy=chosen.uncertainty_policy,
        open_questions=chosen.open_questions,
        dry_run=result,
        compiler=compiler,
        requested_item=chosen.requested_item,
        allowed_item_categories=chosen.allowed_item_categories,
        blocked_item_categories=chosen.blocked_item_categories,
        requires_known_shop=chosen.requires_known_shop,
        nothing_extra=chosen.nothing_extra,
        shop_type=chosen.shop_type,
        single_item=chosen.single_item,
    )


# --- lint and dry-run through engine/interfaces.py (issue #17) ---------------------------
def _register_when_in_contract(name: str) -> Callable[[F], F]:
    """Register ``name`` once the contract lists it; until the ``contract:`` PR lands the
    function is still importable directly, and nothing fails at import."""
    return register(name) if name in INTERFACES else (lambda fn: fn)


@_register_when_in_contract("lint_accepted")
def lint_accepted_ids(rules: list[Rule], accepted_ids: list[str]) -> tuple[list[str], list[str]]:
    """C2 re-lint (api-contract §3.2): ``(missing, reasons)``, both empty when it passes.
    ``missing`` is ``per_order_limit`` first when no per-purchase cap is left, then each
    dropped ``exact`` check id; ``reasons`` are the customer sentences in the same order."""
    kept = [i for i in lint_accepted(rules, accepted_ids).issues
            if i.code in ("no_amount_cap", "exact_check_dropped")]
    return [i.rule_id or i.code for i in kept], [i.message for i in kept]


@_register_when_in_contract("dry_run")
def dry_run_policy(policy: Policy, history: HistoryIndex, card_id: str, customer_id: str) -> DryRunResult:
    """A confirmed or draft policy (instruction or form) over the card's recent history;
    ``agent_history`` is the customer's, across their cards."""
    draft = ParsedDraft(
        instruction=policy.instruction,
        rules=policy.rules,
        uncertainty_policy="decline" if policy.uncertainty_policy == "decline" else "ask",
        open_questions=[],
        requested_item=policy.requested_item,
        allowed_item_categories=policy.allowed_item_categories,
        blocked_item_categories=policy.blocked_item_categories,
        requires_known_shop=policy.requires_known_shop,
        nothing_extra=policy.nothing_extra,
        shop_type=policy.shop_type,
        single_item=policy.single_item,
    )
    return dry_run(draft, history, card_id, customer_id)


__all__ = ["compile_instruction", "dry_run_policy", "lint_accepted_ids"]
