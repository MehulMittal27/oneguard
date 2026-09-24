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
from datetime import date, datetime

from oneguard.api.models import DryRunResult
from oneguard.compiler.draft import ParsedDraft
from oneguard.compiler.dryrun import dry_run
from oneguard.compiler.lint import LintResult, lint, lint_against_floor
from oneguard.compiler.llm import read_with_llm
from oneguard.compiler.parser import parse
from oneguard.compiler.resolve import confirmation_date, simulated_today
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
    *, confirmed_at: datetime | None = None, today: date | None = None,
    preferences: str | None = None,
) -> CompiledDraft:
    """api-contract §3.2 C1 with ``instruction``.

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
