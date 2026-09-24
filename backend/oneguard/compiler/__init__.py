"""Policy compiler (P4): instruction -> typed rules -> lint -> dry-run (rules.md §10).

``compile_instruction`` reads with the LLM (llm.py) and falls back to the English
parser (parser.py) when the model is unavailable, times out, or its reading lints
worse. The instruction is kept verbatim. Lint problems that remain are put to the
customer as open questions; nothing is guessed (T2).
"""

from __future__ import annotations

import logging
from datetime import date

from oneguard.api.models import DryRunResult
from oneguard.compiler.draft import ParsedDraft
from oneguard.compiler.dryrun import dry_run
from oneguard.compiler.lint import LintResult, lint
from oneguard.compiler.llm import read_with_llm
from oneguard.compiler.parser import parse
from oneguard.compiler.resolve import simulated_today
from oneguard.engine.interfaces import register
from oneguard.engine.types import CompiledDraft, HistoryIndex
from oneguard.llm.provider import Provider, ProviderUnavailable, provider_available

log = logging.getLogger(__name__)

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
    text: str, history: HistoryIndex, card_id: str, provider: Provider,
    *, today: date | None = None, preferences: str | None = None,
) -> CompiledDraft:
    """api-contract §3.2 C1 with ``instruction``. ``today`` defaults to the card's
    simulated present (its latest history row, M6)."""
    if today is None:
        today = simulated_today(history, card_id)

    fallback = parse(text, history, card_id, today)
    fallback_lint = lint(fallback)
    chosen, chosen_lint, compiler = fallback, fallback_lint, "fallback"
    if provider_available(provider):
        try:
            read = read_with_llm(text, provider, history, card_id, today, preferences)
            read_lint = lint(read)
            if read_lint.ok or len(read_lint.issues) <= len(fallback_lint.issues):
                chosen, chosen_lint, compiler = read, read_lint, "llm"
            else:
                log.info("compiler: LLM reading linted worse (%s); using fallback",
                         ", ".join(i.code for i in read_lint.issues))
        except ProviderUnavailable as exc:
            log.warning("compiler: LLM unavailable, using fallback: %s", exc)

    chosen = _with_questions(chosen, chosen_lint)
    result: DryRunResult = dry_run(chosen, history, card_id)
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
    )


__all__ = ["compile_instruction"]
