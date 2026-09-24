"""Always-on protections A1–A7 (docs/rules.md §7) as pure signal functions.

``protections(facts, policy, ledger)`` returns one Signal per protection it evaluated,
triggered or not, so the explanation can show what was checked (E7). It only detects:
decide.py turns signals into outcomes (§4 steps 3 and 5). Outcomes asked for here:

- A1 instructions in shop text → ask (decide declines if a rule also fails)
- A2 amounts never come from text → a property, reported untriggered
- A3 duplicate order → ask, related ``duplicate_of``
- A4 split order → ask, related ``split_of``
- A5 re-quote of a declined purchase → info, related ``requote_of``; suppresses A3
- A6 hidden recurring cost → decline when C10 is stated, ask otherwise (Q10)
- A7 lookalike shop → ask (decide declines when C9 applies)

Shop text (``item_name``, ``item_details``, ``merchant_name``) is only ever matched
against the allowlisted patterns below; nothing in it can lower an outcome.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from datetime import timedelta

from oneguard.engine.facts import to_chf
from oneguard.engine.interfaces import register
from oneguard.engine.policy import matches_requested_item
from oneguard.engine.types import (
    Facts,
    ItemFacts,
    LedgerView,
    Policy,
    PriorDecision,
    Signal,
)

DUPLICATE_WINDOW = timedelta(hours=24)
DUPLICATE_AMOUNT_BAND = 0.05
SPLIT_WINDOW = timedelta(minutes=10)
LOOKALIKE_MAX_DISTANCE = 2
RECURRING_CATEGORIES = frozenset({"subscriptions", "membership"})
_LIMIT_FIELD = "authorization.billing_amount_chf"

# A1: imperatives aimed at the agent or the payment system (rules.md §7 examples plus
# common variants). Reused by signals.KeywordSignals. Matching is case-insensitive.
AGENT_DIRECTED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget|override)\b[^.;]{0,40}\b(instructions?|rules?|limits?|checks?|policy|policies)\b",
        r"\bpre[- ]?authori[sz]ed?\b",
        r"\bapprove\b[^.;]{0,30}\b(payment|purchase|transaction|order|charge)\b",
        r"\blimits?\b[^.;]{0,30}\b(do|does|will)\s+not\s+apply\b",
        r"(^|[\s.;:!])system\s*:",
        r"\bcardholder\b[^.;]{0,20}\b(is\s+)?(unavailable|away|unreachable|has\s+(already\s+)?(approved|agreed|authori[sz]ed))\b",
        r"\bnote\s+(for|to)\s+(the\s+)?(automated|ai|purchasing|shopping)\b",
        r"\b(without|skip|bypass)\b[^.;]{0,20}\b(further\s+)?(checks?|verification|confirmation|approval)\b",
        r"\b(you|agents?|assistants?)\s+(must|should|are\s+required\s+to)\s+(approve|pay|complete|buy|purchase)\b",
    )
)


def agent_directed_spans(text: str) -> list[str]:
    """The pieces of ``text`` that read as instructions to the agent (A1)."""
    return [m.group(0) for p in AGENT_DIRECTED_PATTERNS if (m := p.search(text or ""))]


def _shop_texts(facts: Facts) -> list[tuple[str, str]]:
    texts = [("merchant name", facts.merchant_name)]
    for line in facts.items:
        texts.append((f"line {line.line_no} name", line.item_name))
        texts.append((f"line {line.line_no} details", line.item_details))
    return texts


def _a1(facts: Facts, ledger: LedgerView) -> Signal:
    where = []
    for label, text in _shop_texts(facts):
        spans = agent_directed_spans(text)
        if spans:
            where.append(label)
            facts.agent_directed_text.extend(s for s in spans if s not in facts.agent_directed_text)
    if where:
        return Signal(
            id="A1", triggered=True, strength="protection", outcome_if_triggered="ask",
            detail=f"The shop's text contains instructions aimed at the agent ({', '.join(where)}); they were ignored.",
            source="merchant_text",
        )  # fmt: skip
    if facts.merchant_id in ledger.flagged_merchant_ids:
        detail = "An earlier purchase at this shop contained instructions aimed at the agent; this one does not."
    else:
        detail = "No instructions aimed at the agent in the shop's text."
    return Signal(
        id="A1", triggered=False, strength="protection", outcome_if_triggered="ask",
        detail=detail, source="merchant_text",
    )  # fmt: skip


def _a2(facts: Facts) -> Signal:
    # Amounts are plain event fields on Facts; only item facts are FactValues, and none
    # of those is an amount. Kept as evidence so the explanation can say so (E7).
    return Signal(
        id="A2", triggered=False, strength="protection", outcome_if_triggered="decline",
        detail=f"Amount CHF {facts.billing_amount_chf:.2f} taken from the payment request, never from shop text.",
        source="policy",
    )  # fmt: skip


def _may_be_approved(prior: PriorDecision) -> bool:
    """A final approval or a pending step-up (A3, A4).

    A step-up the customer has answered is ``final`` with outcome ``step_up`` whether
    they approved or declined it; PriorDecision does not say which. It is counted, so
    the check can only become more cautious (P5).
    """
    if prior.outcome == "approve":
        return prior.final
    return prior.outcome == "step_up"


def _earlier(facts: Facts, ledger: LedgerView, window: timedelta) -> list[PriorDecision]:
    return [
        p for p in ledger.priors
        if p.authorization_id != facts.authorization_id
        and p.merchant_id == facts.merchant_id
        and facts.timestamp - window <= p.timestamp <= facts.timestamp
        and _may_be_approved(p)
    ]  # fmt: skip


def _requote(facts: Facts, ledger: LedgerView) -> str | None:
    """The declined purchase this one re-quotes, if any (A5)."""
    related = facts.related_authorization_id
    if not related:
        return None
    for prior in ledger.priors:
        if prior.authorization_id == related:
            return related if prior.outcome == "decline" else None
    return related if facts.related_status == "declined" else None


def _a5(facts: Facts, requoted: str | None) -> Signal:
    if requoted:
        return Signal(
            id="A5", triggered=True, strength="protection", outcome_if_triggered="info",
            detail=f"Re-quote of the declined purchase {requoted}; judged on its own facts.",
            source="ledger", related=(requoted, "requote_of"),
        )  # fmt: skip
    return Signal(
        id="A5", triggered=False, strength="protection", outcome_if_triggered="info",
        detail="Not linked to an earlier declined purchase.", source="ledger",
    )  # fmt: skip


def _a3(facts: Facts, ledger: LedgerView, requoted: str | None) -> Signal:
    items = Counter(line.item_id for line in facts.items)
    for prior in reversed(_earlier(facts, ledger, DUPLICATE_WINDOW)):
        if Counter(prior.item_ids) != items or prior.billing_amount_chf <= 0:
            continue
        gap = abs(facts.billing_amount_chf - prior.billing_amount_chf) / prior.billing_amount_chf
        if gap > DUPLICATE_AMOUNT_BAND:
            continue
        hours = (facts.timestamp - prior.timestamp).total_seconds() / 3600
        if requoted:
            break
        return Signal(
            id="A3", triggered=True, strength="protection", outcome_if_triggered="ask",
            detail=(
                f"Same shop and items as {prior.authorization_id} {hours:.1f} h earlier "
                f"(CHF {prior.billing_amount_chf:.2f} then, CHF {facts.billing_amount_chf:.2f} now)."
            ),
            source="ledger", related=(prior.authorization_id, "duplicate_of"),
        )  # fmt: skip
    detail = "A re-quote is not a duplicate." if requoted else "No matching order in the last 24 h."
    return Signal(
        id="A3", triggered=False, strength="protection", outcome_if_triggered="ask",
        detail=detail, source="ledger",
    )  # fmt: skip


def per_order_limit(policy: Policy) -> tuple[float, bool] | None:
    """The strictest stated per-order limit in CHF and whether equal passes (C1, T3, T4)."""
    limits = []
    for rule in policy.rules:
        if rule.field != _LIMIT_FIELD or rule.scope == "period" or rule.operator not in ("<", "<="):
            continue
        if isinstance(rule.value, bool) or not isinstance(rule.value, int | float):
            continue
        value = to_chf(rule.value, rule.currency) if rule.currency and rule.currency != "CHF" else float(rule.value)
        limits.append((value, rule.operator == "<="))
    return min(limits, key=lambda limit: (limit[0], limit[1])) if limits else None


def _exceeds(total: float, limit: tuple[float, bool]) -> bool:
    value, equal_passes = limit
    return total > value if equal_passes else total >= value


def _a4(facts: Facts, policy: Policy, ledger: LedgerView) -> Signal:
    limit = per_order_limit(policy)
    if limit is None:
        return Signal(
            id="A4", triggered=False, strength="protection", outcome_if_triggered="ask",
            detail="No per-order limit stated, so there is nothing to split around.", source="ledger",
        )  # fmt: skip
    for prior in reversed(_earlier(facts, ledger, SPLIT_WINDOW)):
        combined = round(prior.billing_amount_chf + facts.billing_amount_chf, 2)
        if _exceeds(combined, limit):
            minutes = (facts.timestamp - prior.timestamp).total_seconds() / 60
            return Signal(
                id="A4", triggered=True, strength="protection", outcome_if_triggered="ask",
                detail=(
                    f"{minutes:.0f} min after {prior.authorization_id} at the same shop; together "
                    f"CHF {combined:.2f}, over the CHF {limit[0]:.2f} per-order limit."
                ),
                source="ledger", related=(prior.authorization_id, "split_of"),
            )  # fmt: skip
    return Signal(
        id="A4", triggered=False, strength="protection", outcome_if_triggered="ask",
        detail="No order at this shop in the last 10 min that would add up past the limit.",
        source="ledger",
    )  # fmt: skip


def _is_recurring(line: ItemFacts) -> bool:
    return line.item_category in RECURRING_CATEGORIES or bool(line.recurring.known and line.recurring.value)


def _asked_for(line: ItemFacts, policy: Policy) -> bool:
    if policy.requested_item and matches_requested_item(line, policy.requested_item):
        return True
    return bool(policy.allowed_item_categories and line.item_category in policy.allowed_item_categories)


def _a6(facts: Facts, policy: Policy) -> Signal:
    outcome = "decline" if policy.nothing_extra else "ask"
    capable = "can" if facts.merchant_recurring_capable else "cannot"
    hidden = [line for line in facts.items if _is_recurring(line) and not _asked_for(line, policy)]
    if hidden:
        names = ", ".join(
            f"line {line.line_no} (CHF {line.unit_price_chf * line.quantity:.2f})" for line in hidden
        )
        return Signal(
            id="A6", triggered=True, strength="protection", outcome_if_triggered=outcome,
            detail=f"Recurring charge you did not ask for: {names}. The shop {capable} bill repeatedly.",
            source="merchant_text",
        )  # fmt: skip
    return Signal(
        id="A6", triggered=False, strength="protection", outcome_if_triggered=outcome,
        detail=f"No recurring charge you did not ask for. The shop {capable} bill repeatedly.",
        source="merchant_text",
    )  # fmt: skip


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (A7)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalise_name(name: str) -> str:
    """Same normalisation as store.history.normalise_merchant_name (A7)."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed.casefold() if ch.isascii() and ch.isalnum())


def lookalike(facts: Facts, known_names: Mapping[str, str]) -> tuple[str, int] | None:
    """A known shop (id, distance) whose normalised name is within 2 edits of this one."""
    if facts.merchant_id in known_names:
        return None
    mine = normalise_name(facts.merchant_name)
    best = None
    for merchant_id, name in known_names.items():
        distance = edit_distance(mine, normalise_name(name))
        if distance <= LOOKALIKE_MAX_DISTANCE and (best is None or distance < best[1]):
            best = (merchant_id, distance)
    return best


def _a7(facts: Facts, policy: Policy, known_names: Mapping[str, str] | None) -> Signal:
    outcome = "decline" if policy.requires_known_shop else "ask"
    if known_names is None:
        # LedgerView carries known merchant ids but not their names; the comparison
        # waits for that field (contract request). C9 still stops unknown shops.
        return Signal(
            id="A7", triggered=False, strength="protection", outcome_if_triggered=outcome,
            detail="Lookalike check not run: known shop names are not available.", source="ledger",
        )  # fmt: skip
    match = lookalike(facts, known_names)
    if match:
        merchant_id, distance = match
        return Signal(
            id="A7", triggered=True, strength="protection", outcome_if_triggered=outcome,
            detail=(
                f"This shop's name is {distance} letter(s) away from a shop you know "
                f"({merchant_id}), but it is a different shop."
            ),
            source="ledger",
        )  # fmt: skip
    return Signal(
        id="A7", triggered=False, strength="protection", outcome_if_triggered=outcome,
        detail="Name does not resemble another shop you know.", source="ledger",
    )  # fmt: skip


def evaluate(
    facts: Facts, policy: Policy, ledger: LedgerView, known_names: Mapping[str, str] | None = None
) -> list[Signal]:
    """A1–A7. ``known_names`` (merchant id → name) enables A7."""
    requoted = _requote(facts, ledger)
    return [
        _a1(facts, ledger),
        _a2(facts),
        _a3(facts, ledger, requoted),
        _a4(facts, policy, ledger),
        _a5(facts, requoted),
        _a6(facts, policy),
        _a7(facts, policy, known_names),
    ]


@register("protections")
def protections(facts: Facts, policy: Policy, ledger: LedgerView) -> list[Signal]:
    return evaluate(facts, policy, ledger)
