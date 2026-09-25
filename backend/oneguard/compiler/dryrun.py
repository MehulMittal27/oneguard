"""Dry-run a draft over the card's recent history (rules.md T5, api-contract §2 DryRunResult).

Replays the typed rules over ``HistoryIndex.recent_rows(card_id, 90)`` (no CSV) with a
minimal evaluator of its own: history rows carry the order total, time, shop and
country, not cart lines or order terms. A known-shop check (a rule on
``merchant.known_shop`` / ``merchant.familiar_on_card``, or the ``requires_known_shop``
flag alone) makes a purchase at a shop the card had not bought from before ``ask``, as
the form preview does (api/policies.py ``form_dry_run``): the card's history only, so it
can ask more than the engine's customer-level C9 (Q7). Rules history cannot show (item details, delivery, returns) are left out of the verdict
and named in the insight; rules no data can ever check make the row ``ask``. A preview for the customer, never a
decision: the engine decides at purchase time.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from oneguard.api.models import AgentHistory, DryRunExample, DryRunResult
from oneguard.compiler.draft import (
    COUNT_FIELD,
    WEEKDAYS,
    ParsedDraft,
    fmt_amount,
    human,
    to_chf,
)
from oneguard.engine.policy import KNOWN_SHOP_FIELDS, is_last_price_rule
from oneguard.engine.types import HistoryIndex, HistoryRow, Rule

WINDOW_DAYS = 90
ALL_DAYS = 36500
ZURICH = ZoneInfo("Europe/Zurich")
_ORDER = {"violate": 0, "ask": 1, "fit": 2}


def _cmp(a: Decimal, op: str, b: Decimal) -> bool:
    return {"<": a < b, "<=": a <= b, "=": a == b, "!=": a != b, ">": a > b, ">=": a >= b}[op]


def _known_shop(row: HistoryRow, seen: set[str]) -> tuple[str, str]:
    """C9 on a past purchase: a shop bought from before on this card fits, a new one asks."""
    if row.merchant_id in seen:
        return "fit", "a shop you know"
    return "ask", "a shop not bought from before on this card"


def _last_price(rule: Rule, row: HistoryRow, prior: list[HistoryRow]) -> tuple[str, str]:
    """"Same price as last time at this shop" on a past purchase: against the card's last
    approved purchase at the same shop before it; the first one there asks, as the engine does."""
    last = next((p for p in reversed(prior) if p.merchant_id == row.merchant_id), None)
    if last is None:
        return "ask", f"first payment at {row.merchant_name}, no earlier price"
    amount, before = Decimal(str(row.billing_amount_chf)), Decimal(str(last.billing_amount_chf))
    reason = f"CHF {fmt_amount(amount)} vs CHF {fmt_amount(before)} last time"
    if _cmp(amount, rule.operator, before):
        return "fit", reason
    return ("ask" if rule.on_fail == "ask" else "violate"), reason


def _check(rule: Rule, row: HistoryRow, prior: list[HistoryRow], seen: set[str]) -> tuple[str, str] | None:
    """(verdict, reason) for one rule on one past purchase, or None if history can't show it."""
    f, op = rule.field, rule.operator
    local = row.timestamp.astimezone(ZURICH)
    amount = Decimal(str(row.billing_amount_chf))
    if is_last_price_rule(rule):
        return _last_price(rule, row, prior)
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
    if f == COUNT_FIELD and rule.period_days:  # approved purchases on the card in the window, this one too
        start = row.timestamp - timedelta(days=rule.period_days)
        n = 1 + sum(1 for p in prior if p.timestamp >= start)
        ok = _cmp(Decimal(n), op, Decimal(str(rule.value)))
        return ("fit" if ok else "violate", f"purchase {n} within {rule.period_days} day(s)")
    if f == "merchant.merchant_category":
        ok = (row.merchant_category == rule.value) == (op == "=")
        return ("fit" if ok else "violate", f"{human(row.merchant_category)} shop")
    if f == "merchant.merchant_country":
        ok = (row.merchant_country == rule.value) == (op == "=")
        return ("fit" if ok else "violate", f"shop in {row.merchant_country}")
    if f in KNOWN_SHOP_FIELDS:  # api-contract §3.3: the same check
        return _known_shop(row, seen)
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


def dry_run(draft: ParsedDraft, history: HistoryIndex, card_id: str, customer_id: str | None) -> DryRunResult:
    """The draft over the card's last 90 days. ``agent_history`` is the customer's across
    their cards; with no ``customer_id`` it is the card's customer, when history has one."""
    everything = history.recent_rows(card_id, ALL_DAYS) if card_id else []
    approved = sorted((r for r in everything if r.transaction_type == "purchase" and r.status == "approved"),
                      key=lambda r: (r.timestamp, r.authorization_id))
    window = {r.authorization_id for r in history.recent_rows(card_id, WINDOW_DAYS)} if card_id else set()
    customer = customer_id or next((r.customer_id for r in everything), None)
    agent = history.agent_history(customer) if customer else None
    flag_only = draft.requires_known_shop and not any(r.field in KNOWN_SHOP_FIELDS for r in draft.rules)

    counts = {"fit": 0, "violate": 0, "ask": 0}
    rows: list[HistoryRow] = []
    examples: list[tuple[str, HistoryRow, str]] = []
    unchecked: set[str] = set()
    seen: set[str] = set()  # shops the card bought from before the row being checked
    for i, row in enumerate(approved):
        if row.authorization_id not in window:
            seen.add(row.merchant_id)
            continue
        rows.append(row)
        verdicts = [_known_shop(row, seen)] if flag_only else []
        for rule in draft.rules:
            result = _check(rule, row, approved[:i], seen)
            if result is None:
                unchecked.add(rule.text)
            else:
                verdicts.append(result)
        seen.add(row.merchant_id)
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
