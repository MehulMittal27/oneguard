"""Policies behind C1–C5 (docs/api-contract.md §3.2, §3.3, §3.9; docs/rules.md §10).

What the UI sees of a policy is ``RuleCheck`` text; what the engine applies is the typed
``Rule`` behind each check id. Both live in the store: ``checks`` (ordered RuleChecks)
and ``rules`` (``{check id: typed rule}``). The Policy flags that restate a rule or have
no field of their own (``requested_item``, ``nothing_extra``, …) are kept in ``rules``
under ``POLICY_KEY``, which is never a check id.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from oneguard.api import models as api
from oneguard.engine.ledger_base import LedgerEntry
from oneguard.engine.types import CompiledDraft, HistoryIndex, Policy, Rule

POLICY_KEY = "__policy__"
"""Key of the Policy flags inside a stored ``rules`` object."""

AMOUNT_FIELD = "authorization.billing_amount_chf"
KNOWN_SHOP_FIELDS = ("merchant.known_shop", "merchant.familiar_on_card")
PER_ORDER_OPERATORS = ("<", "<=")
DRY_RUN_DAYS = 90
ALL_HISTORY_DAYS = 36500
NO_CAP_QUESTION = "No amount stated: what is the most one purchase may cost?"

FX_TO_CHF = {"CHF": Decimal(1), "EUR": Decimal("0.95"), "GBP": Decimal("1.12"), "USD": Decimal("0.87")}
"""rules.md M1: the fixed conversion rates."""

_FLAGS = (
    "requested_item",
    "allowed_item_categories",
    "blocked_item_categories",
    "requires_known_shop",
    "nothing_extra",
    "shop_type",
)
_FLAG_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "allowed_item_categories": (("items[].item_category",), ("in",)),
    "blocked_item_categories": (("items[].item_category",), ("not_in",)),
    "shop_type": (("merchant.merchant_category",), ("=", "in")),
    "requires_known_shop": (KNOWN_SHOP_FIELDS, ("=",)),
}
"""The rule a flag restates, as (fields, operators): dropping every such check clears it."""
_CLEARED: dict[str, Any] = {
    "allowed_item_categories": None,
    "blocked_item_categories": None,
    "shop_type": None,
    "requires_known_shop": False,
}


def chf(value: float | Decimal) -> str:
    """An amount as §3.9 words it: whole francs plain, otherwise two decimals."""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return f"{amount:.0f}" if amount == amount.to_integral_value() else f"{amount:.2f}"


def to_chf(value: float, currency: str | None) -> float:
    rate = FX_TO_CHF.get(currency or "CHF", Decimal(1))
    return float((Decimal(str(value)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def rule_check(rule: Rule) -> api.RuleCheck:
    return api.RuleCheck(id=rule.id, text=rule.text, source=rule.source, uncertainty=rule.uncertainty, kind=rule.kind)


def flags_of(draft: CompiledDraft | Policy) -> dict[str, Any]:
    return {name: getattr(draft, name) for name in _FLAGS}


def store_rules(rules: Sequence[Rule], flags: dict[str, Any]) -> dict[str, Any]:
    """The stored ``rules`` object: typed rules by check id plus the Policy flags."""
    stored: dict[str, Any] = {r.id: r.model_dump(mode="json") for r in rules}
    stored[POLICY_KEY] = flags
    return stored


def load_rules(stored: dict[str, Any], checks: Iterable[dict[str, Any]]) -> tuple[list[Rule], dict[str, Any]]:
    """Typed rules in check order, and the Policy flags, from a stored draft or mandate."""
    rules = [Rule.model_validate(stored[c["id"]]) for c in checks if c["id"] in stored]
    flags = {name: None for name in _FLAGS} | {"requires_known_shop": False, "nothing_extra": False}
    flags.update(stored.get(POLICY_KEY) or {})
    return rules, flags


def policy_of(mandate_id: str, status: str, instruction: str, rules: list[Rule], flags: dict[str, Any], uncertainty: str) -> Policy:
    return Policy(
        mandate_id=mandate_id,
        status="active" if status == "active" else "revoked",
        instruction=instruction,
        rules=rules,
        uncertainty_policy=uncertainty,
        **flags,
    )


def accepted_flags(flags: dict[str, Any], draft_rules: Sequence[Rule], accepted: Sequence[Rule]) -> dict[str, Any]:
    """The flags that still hold once the customer kept only ``accepted`` (C2).

    A flag whose restated check the customer dropped is cleared; a flag with no check of
    its own (``requested_item``, ``nothing_extra``) stays: it only ever adds a restriction.
    """
    kept = dict(flags)
    for name, (fields, operators) in _FLAG_RULES.items():
        def restates(rule: Rule, fields: tuple[str, ...] = fields, operators: tuple[str, ...] = operators) -> bool:
            return rule.field in fields and rule.operator in operators

        if any(restates(r) for r in draft_rules) and not any(restates(r) for r in accepted):
            kept[name] = _CLEARED[name]
    return kept


def per_order_cap(rules: Iterable[Rule]) -> tuple[float, str] | None:
    """The tightest per-purchase amount cap in CHF and its operator, if any (C1)."""
    caps = [
        (to_chf(float(r.value), r.currency), r.operator)
        for r in rules
        if r.field == AMOUNT_FIELD
        and r.operator in PER_ORDER_OPERATORS
        and r.scope in (None, "purchase")
        and isinstance(r.value, (int, float))
    ]
    return min(caps, key=lambda c: (c[0], c[1] == "<=")) if caps else None


def period_limit(rules: Iterable[Rule]) -> tuple[float, int] | None:
    """(limit CHF, days) of the shortest period rule (C2); the lowest limit on a tie."""
    limits = [
        (to_chf(float(r.value), r.currency), int(r.period_days))
        for r in rules
        if r.field == AMOUNT_FIELD
        and r.scope == "period"
        and r.period_days
        and r.operator in PER_ORDER_OPERATORS
        and isinstance(r.value, (int, float))
    ]
    return min(limits, key=lambda x: (x[1], x[0])) if limits else None


def relint(draft_checks: Sequence[api.RuleCheck], accepted: Sequence[Rule]) -> tuple[list[str], list[str]]:
    """C2 re-lint of the accepted subset: (missing, reasons); both empty when it passes.

    ``missing`` names ``per_order_limit`` when no per-purchase amount cap is left and the
    id of every dropped check whose source is ``exact``.
    """
    missing: list[str] = []
    reasons: list[str] = []
    if per_order_cap(accepted) is None:
        missing.append("per_order_limit")
        reasons.append("the policy needs a limit on what one purchase may cost")
    kept = {r.id for r in accepted}
    for check in draft_checks:
        if check.source == "exact" and check.id not in kept:
            missing.append(check.id)
            reasons.append(f'you stated "{check.text}" and it was left out')
    return missing, reasons


# C1 with a form: rules built directly, no model -----------------------------------------


def form_rules(form: api.FormInput) -> tuple[list[Rule], dict[str, Any]]:
    """Typed rules and Policy flags for the form path, worded as §3.9 requires."""
    rules: list[Rule] = []
    flags: dict[str, Any] = {name: None for name in _FLAGS} | {"requires_known_shop": False, "nothing_extra": False}
    if form.per_order_limit_chf is not None:
        rules.append(
            Rule(
                id="per_order_limit", field=AMOUNT_FIELD, operator="<=", value=form.per_order_limit_chf,
                currency="CHF", scope="purchase", source="exact", kind="amount",
                text=f"Total at or below CHF {chf(form.per_order_limit_chf)} per order",
            )
        )  # fmt: skip
    if form.period_limit_chf is not None and form.period_days is not None:
        rules.append(
            Rule(
                id="period_limit", field=AMOUNT_FIELD, operator="<=", value=form.period_limit_chf,
                currency="CHF", scope="period", period_days=form.period_days, source="exact", kind="period",
                text=f"Total at or below CHF {chf(form.period_limit_chf)} across any {form.period_days} days",
            )
        )  # fmt: skip
    if form.categories:
        categories = sorted(dict.fromkeys(form.categories))
        rules.append(
            Rule(
                id="item_categories", field="items[].item_category", operator="in", value=categories,
                source="exact", kind="item",
                text="Only these purchase types: " + ", ".join(c.replace("_", " ") for c in categories),
            )
        )  # fmt: skip
        flags["allowed_item_categories"] = categories
    if form.sellers_used_before_only:
        rules.append(
            Rule(
                id="known_shop", field="merchant.known_shop", operator="=", value="true",
                source="exact", kind="merchant", text="Only from shops you have bought from before",
            )
        )  # fmt: skip
        flags["requires_known_shop"] = True
    return rules, flags


def form_instruction(rules: Sequence[Rule], uncertainty: str) -> str:
    """The form's rules as one plain sentence list (the form has no free text)."""
    ask = "Ask me when uncertain." if uncertainty == "ask" else "Decline when uncertain."
    return " ".join([*(f"{r.text}." for r in rules), ask])


def form_dry_run(rules: Sequence[Rule], flags: dict[str, Any], history: HistoryIndex, card_id: str, customer_id: str) -> api.DryRunResult:
    """The form's rules against the card's last 90 days of approved purchases.

    Only what history can answer is judged: the per-order cap (fit / violate) and, when
    asked for, whether the card had bought at that shop before (ask when it had not; the
    card's history only, so this can ask more than the engine's customer-level C9).
    Item categories are not in history, so they are not judged here.
    """
    approved = sorted(
        (
            r
            for r in history.recent_rows(card_id, ALL_HISTORY_DAYS)
            if r.transaction_type == "purchase" and r.status == "approved"
        ),
        key=lambda r: r.timestamp,
    )
    window = {r.authorization_id for r in history.recent_rows(card_id, DRY_RUN_DAYS)}
    rows = [r for r in approved if r.authorization_id in window]
    cap = per_order_cap(rules)
    known_only = bool(flags.get("requires_known_shop"))
    seen = {r.merchant_id for r in approved if r.authorization_id not in window}
    results: list[tuple[Any, str, str]] = []
    for row in rows:
        if cap is not None and not (row.billing_amount_chf < cap[0] if cap[1] == "<" else row.billing_amount_chf <= cap[0]):
            results.append((row, "violate", f"CHF {chf(row.billing_amount_chf)} is over CHF {chf(cap[0])}."))
        elif known_only and row.merchant_id not in seen:
            results.append((row, "ask", "A shop not bought from before on this card."))
        else:
            results.append((row, "fit", "Within your limits."))
        seen.add(row.merchant_id)
    counts = {k: sum(1 for _, o, _ in results if o == k) for k in ("fit", "violate", "ask")}
    if not results:
        insight = "No approved purchases on this card in the last 90 days to check these rules against."
    elif counts["violate"]:
        insight = (
            f"{counts['violate']} of your last {len(results)} purchases on this card would have been stopped "
            f"by these rules."
        )
    else:
        insight = f"All {len(results)} of your last purchases on this card fit these rules."
    order = {"violate": 0, "ask": 1, "fit": 2}
    examples = sorted(results, key=lambda x: (order[x[1]], -x[0].timestamp.timestamp()))[:3]
    attempts, approved = history.agent_history(customer_id)
    return api.DryRunResult(
        sample_size=len(results),
        would_violate=counts["violate"],
        would_fit=counts["fit"],
        would_ask=counts["ask"],
        insight=insight,
        examples=[
            api.DryRunExample(
                occurred_at=row.timestamp,
                merchant_name=row.merchant_name,
                billing_amount_chf=row.billing_amount_chf,
                outcome=outcome,
                reason=reason,
            )
            for row, outcome, reason in examples
        ],
        agent_history=api.AgentHistory(attempts=attempts, approved=approved),
    )


# C3 usage -------------------------------------------------------------------------------


def usage(rules: Sequence[Rule], entries: Sequence[LedgerEntry], confirmed_at: datetime) -> api.MandateUsage:
    """``Mandate.usage`` from the ledger entries of the mandate's latest run (§2, M4, M5).

    ``as_of`` is the simulated time of the last decision (``confirmed_at`` before any).
    Spent counts final approvals only (customer-approved step-ups included), pending the
    reservations of step-ups still waiting, both within the period window ending at
    ``as_of``; with no period rule the window is the whole run.
    """
    cap = per_order_cap(rules)
    period = period_limit(rules)
    if entries:
        as_of = max(e.ts_sim for e in entries)
        start = as_of - timedelta(days=period[1]) if period else min(e.ts_sim for e in entries)
    else:
        as_of = start = confirmed_at
    window = [e for e in entries if start <= e.ts_sim <= as_of]
    spent = sum((Decimal(str(e.spent_chf)) for e in window), Decimal(0))
    pending = sum((Decimal(str(e.reserved_chf)) for e in window), Decimal(0))
    return api.MandateUsage(
        per_order_limit_chf=cap[0] if cap else None,
        period_limit_chf=period[0] if period else None,
        period_days=period[1] if period else None,
        period_spent_chf=float(spent),
        period_window_start=start,
        pending_chf=float(pending),
        fulfilment=None,
        as_of=as_of,
    )
