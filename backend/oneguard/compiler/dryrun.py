"""Dry-run a draft over the card's recent history (rules.md T5, api-contract §2 DryRunResult).

Replays the typed rules over ``HistoryIndex.recent_rows(card_id, 90)`` (no CSV) with a
minimal evaluator of its own: history rows carry the order total, time, shop, country
and familiarity (customer level over all history, Q7), not cart lines or order terms.
Rules history cannot show (item details, delivery, returns) are left out of the verdict
and named in the insight; rules no data can ever check make the row ``ask``. A preview for the customer, never a
decision: the engine decides at purchase time.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from oneguard.api.models import AgentHistory, DryRunExample, DryRunResult
from oneguard.compiler.draft import (
    KNOWN_SHOP_FIELD,
    WEEKDAYS,
    ParsedDraft,
    fmt_amount,
    human,
    to_chf,
)
from oneguard.engine.types import HistoryIndex, HistoryRow, Rule

WINDOW_DAYS = 90
ZURICH = ZoneInfo("Europe/Zurich")
_ORDER = {"violate": 0, "ask": 1, "fit": 2}


def _cmp(a: Decimal, op: str, b: Decimal) -> bool:
    return {"<": a < b, "<=": a <= b, "=": a == b, "!=": a != b, ">": a > b, ">=": a >= b}[op]


def _check(rule: Rule, row: HistoryRow, prior: list[HistoryRow], known: set[str]) -> tuple[str, str] | None:
    """(verdict, reason) for one rule on one past purchase, or None if history can't show it."""
    f, op = rule.field, rule.operator
    local = row.timestamp.astimezone(ZURICH)
    amount = Decimal(str(row.billing_amount_chf))
    if f == "authorization.billing_amount_chf":
        limit = to_chf(rule.value, rule.currency)
        if rule.scope == "period":
            start = row.timestamp - timedelta(days=rule.period_days or 7)
            total = amount + sum((Decimal(str(p.billing_amount_chf)) for p in prior if p.timestamp >= start),
                                 Decimal(0))
            ok = _cmp(total, op, limit)
            return ("fit" if ok else "violate",
                    f"{rule.period_days or 7}-day total CHF {fmt_amount(total)} vs CHF {fmt_amount(limit)}")
        ok = _cmp(amount, op, limit)
        if not ok and rule.on_fail == "ask":
            return "ask", f"CHF {fmt_amount(amount)} differs from CHF {fmt_amount(limit)}"
        return ("fit" if ok else "violate", f"CHF {fmt_amount(amount)} vs CHF {fmt_amount(limit)}")
    if f == "merchant.merchant_category":
        ok = (row.merchant_category == rule.value) == (op == "=")
        return ("fit" if ok else "violate", f"{human(row.merchant_category)} shop")
    if f == "merchant.merchant_country":
        ok = (row.merchant_country == rule.value) == (op == "=")
        return ("fit" if ok else "violate", f"shop in {row.merchant_country}")
    if f in (KNOWN_SHOP_FIELD, "merchant.known_shop"):  # api-contract §3.3: the same check
        ok = row.merchant_id in known
        return ("fit" if ok else "violate", "a shop you know" if ok else "a shop you have not bought from")
    if f == "authorization.weekday":
        day = WEEKDAYS[local.weekday()]
        ok = (day in rule.value) == (op == "in")
        return ("fit" if ok else "violate", f"on a {day.capitalize()}")
    if f == "authorization.local_hour":
        ok = _cmp(Decimal(local.hour), op, Decimal(str(rule.value)))
        return ("fit" if ok else "violate", f"at {local:%H:%M}")
    if f == "unverifiable":
        return "ask", f'"{rule.value}" needs your answer'
    return None


def dry_run(draft: ParsedDraft, history: HistoryIndex, card_id: str) -> DryRunResult:
    rows = [r for r in (history.recent_rows(card_id, WINDOW_DAYS) if card_id else [])
            if r.transaction_type == "purchase" and r.status == "approved"]
    rows.sort(key=lambda r: (r.timestamp, r.authorization_id))
    customer = rows[0].customer_id if rows else None
    known = set(history.known_merchants(customer)) if customer else set()
    agent = history.agent_history(customer) if customer else None

    counts = {"fit": 0, "violate": 0, "ask": 0}
    examples: list[tuple[str, HistoryRow, str]] = []
    unchecked: set[str] = set()
    for i, row in enumerate(rows):
        verdicts = []
        for rule in draft.rules:
            result = _check(rule, row, rows[:i], known)
            if result is None:
                unchecked.add(rule.text)
            else:
                verdicts.append(result)
        worst = min(verdicts, key=lambda v: _ORDER[v[0]], default=("fit", "no rule history can show"))
        outcome = worst[0]
        counts[outcome] += 1
        reason = worst[1] if outcome != "fit" else "meets every check history can show"
        examples.append((outcome, row, reason))

    examples.sort(key=lambda e: (_ORDER[e[0]], -e[1].timestamp.timestamp()))
    shown = [
        DryRunExample(occurred_at=row.timestamp, merchant_name=row.merchant_name,
                      billing_amount_chf=row.billing_amount_chf, outcome=outcome, reason=reason)
        for outcome, row, reason in examples[:3]
    ]
    if rows:
        insight = (f"Of your last {len(rows)} purchases on this card ({WINDOW_DAYS} days), "
                   f"{counts['fit']} would fit, {counts['violate']} would break a rule"
                   f" and {counts['ask']} would need your answer.")
        if unchecked:
            insight += " Item details, returns and delivery are checked at purchase time."
    else:
        insight = f"No purchases on this card in the last {WINDOW_DAYS} days to test these rules against."
    return DryRunResult(
        sample_size=len(rows),
        would_violate=counts["violate"],
        would_fit=counts["fit"],
        would_ask=counts["ask"],
        insight=insight,
        examples=shown or None,
        agent_history=AgentHistory(attempts=agent[0], approved=agent[1]) if agent else None,
    )
