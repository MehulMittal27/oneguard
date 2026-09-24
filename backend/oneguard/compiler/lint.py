"""Lint a compiled draft before the customer sees it (rules.md §10, api-contract §3.2).

- T1 every amount the customer wrote is used by a rule or raised as a question;
- T2 no invented limits: every stated number in a rule appears in the instruction,
  unless the compiler resolved it (``ParsedDraft.resolved``: history, a date);
- T3 boundary words kept: "under" is ``<``, "at or below / or less / max / up to" is ``<=``;
- T4 a foreign-currency limit is shown converted to CHF;
- T5 a per-order amount cap is present, or an open question asks for one;
- contradictions: rules on the same field that no purchase could satisfy together.

``lint_accepted`` is the C2 re-lint of the subset the customer accepted.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from oneguard.compiler.draft import MONEY_FIELDS, ParsedDraft, to_chf
from oneguard.compiler.parser import (
    _AMOUNT,
    _EXACT_BEFORE,
    _INCLUSIVE_AFTER,
    _INCLUSIVE_BEFORE,
    _STRICT_BEFORE,
    NUMBER_WORDS,
)
from oneguard.engine.types import Rule

IssueCode = Literal[
    "amount_not_used", "invented_value", "boundary_changed", "currency_not_shown",
    "no_amount_cap", "contradiction", "exact_check_dropped",
]
_AMOUNT_QUESTION = re.compile(r"\b(?:amount|limit|cost|price|spend|budget|CHF)\b", re.IGNORECASE)
_STATED_NUMBERS = {"items[].size_eu", "order.return_window_days", "cart.quantity", "items[].quantity"}


class LintIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: IssueCode
    message: str
    rule_id: str | None = None


class LintResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[LintIssue]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def missing(self) -> list[str]:
        """For the 409 envelope (``detail.missing``): what the accepted set lacks."""
        return [i.rule_id or i.code for i in self.issues if i.code in ("no_amount_cap", "exact_check_dropped")]


def _stated_amounts(text: str) -> list[tuple[Decimal, str | None]]:
    """(value, expected operator) for each amount in the instruction."""
    out = []
    for m in _AMOUNT.finditer(text):
        raw = (m.group("num") or m.group("num2")).replace(",", "").replace("'", "")
        before, after = text[: m.start()], text[m.end():]
        if _EXACT_BEFORE.search(before):
            op = "="
        elif _INCLUSIVE_AFTER.search(after) or _INCLUSIVE_BEFORE.search(before):
            op = "<="
        elif _STRICT_BEFORE.search(before):
            op = "<"
        else:
            op = None
        out.append((Decimal(raw), op))
    return out


def _numbers_in(text: str) -> set[Decimal]:
    found = {Decimal(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text.replace("'", ""))}
    found |= {Decimal(n) for w, n in NUMBER_WORDS.items() if re.search(rf"\b{w}\b", text, re.IGNORECASE)}
    found |= {Decimal(n.replace(",", "")) for n in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text)}
    return found


def _has_amount_cap(rules: list[Rule]) -> bool:
    return any(r.field == "authorization.billing_amount_chf" and r.scope != "period" for r in rules)


def _check_boundaries(draft: ParsedDraft) -> list[LintIssue]:
    issues: list[LintIssue] = []
    stated = _stated_amounts(draft.instruction)
    for rule in draft.rules:
        if rule.field not in MONEY_FIELDS or rule.id in draft.resolved:
            continue
        value = Decimal(str(rule.value))
        ops = {op for v, op in stated if v == value}
        if not ops and not any(v == value for v, _ in stated):
            issues.append(LintIssue(code="invented_value", rule_id=rule.id,
                                    message=f"{rule.text}: the amount is not in your instruction"))
            continue
        expected = {op for op in ops if op}
        if expected and rule.operator not in expected:
            issues.append(LintIssue(code="boundary_changed", rule_id=rule.id,
                                    message=f'{rule.text}: your wording means "{"/".join(sorted(expected))}"'))
        if rule.currency and rule.currency != "CHF" and "CHF" not in rule.text:
            issues.append(LintIssue(code="currency_not_shown", rule_id=rule.id,
                                    message=f"{rule.text}: the CHF equivalent is not shown"))
    return issues


def _check_numbers(draft: ParsedDraft) -> list[LintIssue]:
    numbers = _numbers_in(draft.instruction)
    return [
        LintIssue(code="invented_value", rule_id=r.id, message=f"{r.text}: {r.value} is not in your instruction")
        for r in draft.rules
        if r.field in _STATED_NUMBERS and r.id not in draft.resolved and Decimal(str(r.value)) not in numbers
    ]


def _check_amounts_used(draft: ParsedDraft) -> list[LintIssue]:
    used = {Decimal(str(r.value)) for r in draft.rules if r.field in MONEY_FIELDS}
    asked = " ".join(draft.open_questions)
    issues = []
    for value, _ in _stated_amounts(draft.instruction):
        if value not in used and str(value) not in asked:
            issues.append(LintIssue(code="amount_not_used",
                                    message=f"You wrote {value}, but no check uses it"))
    return issues


def _bounds_conflict(rules: list[Rule]) -> list[LintIssue]:
    issues = []
    groups: dict[tuple, list[Rule]] = {}
    for r in rules:
        if r.field == "unverifiable":
            continue
        groups.setdefault((r.field, r.scope, r.period_days), []).append(r)
    for (field, _, _), group in groups.items():
        if len(group) < 2:
            continue
        ids = ", ".join(r.id for r in group)
        numeric = [r for r in group if isinstance(r.value, (int, float))]
        if numeric:
            money = field in MONEY_FIELDS

            def chf(r: Rule, money: bool = money) -> Decimal:
                return to_chf(r.value, r.currency) if money else Decimal(str(r.value))

            lows = [(chf(r), r.operator) for r in numeric if r.operator in (">", ">=")]
            highs = [(chf(r), r.operator) for r in numeric if r.operator in ("<", "<=")]
            equals = {chf(r) for r in numeric if r.operator == "="}
            lo = max(lows, default=None, key=lambda x: (x[0], x[1] == ">"))
            hi = min(highs, default=None, key=lambda x: (x[0], x[1] == "<="))
            bad = len(equals) > 1
            if lo and hi and (lo[0] > hi[0] or (lo[0] == hi[0] and (lo[1] == ">" or hi[1] == "<"))):
                bad = True
            for e in equals:
                if (lo and (e < lo[0] or (e == lo[0] and lo[1] == ">"))) or \
                   (hi and (e > hi[0] or (e == hi[0] and hi[1] == "<"))):
                    bad = True
            if bad:
                issues.append(LintIssue(code="contradiction", message=f"Checks {ids} cannot all be met"))
            continue
        allowed = [set(r.value) for r in group if r.operator == "in" and isinstance(r.value, list)]
        blocked = set().union(*[set(r.value) for r in group if r.operator == "not_in" and isinstance(r.value, list)])
        equal = {str(r.value) for r in group if r.operator == "="}
        if allowed and not (set.intersection(*allowed) - blocked):
            issues.append(LintIssue(code="contradiction", message=f"Checks {ids} leave nothing allowed"))
        elif len(equal) > 1:
            issues.append(LintIssue(code="contradiction", message=f"Checks {ids} ask for different values"))
    return issues


def lint(draft: ParsedDraft) -> LintResult:
    issues = _check_amounts_used(draft) + _check_boundaries(draft) + _check_numbers(draft)
    if not _has_amount_cap(draft.rules) and not any(_AMOUNT_QUESTION.search(q) for q in draft.open_questions):
        issues.append(LintIssue(code="no_amount_cap",
                                message="No per-order amount limit, and no question asking for one"))
    issues += _bounds_conflict(draft.rules)
    return LintResult(issues=issues)


def lint_accepted(rules: list[Rule], accepted_ids: list[str]) -> LintResult:
    """C2: the accepted subset must keep a per-order amount cap and every ``exact`` check."""
    accepted = set(accepted_ids)
    kept = [r for r in rules if r.id in accepted]
    issues = [
        LintIssue(code="exact_check_dropped", rule_id=r.id, message=f'"{r.text}" is what you asked for')
        for r in rules if r.source == "exact" and r.id not in accepted
    ]
    if not _has_amount_cap(kept):
        issues.insert(0, LintIssue(code="no_amount_cap", rule_id="per_order_limit",
                                   message="A per-order amount limit is required"))
    issues += _bounds_conflict(kept)
    return LintResult(issues=issues)
