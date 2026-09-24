"""Explanations (docs/rules.md §9, E1–E7): one plain sentence, the evidence, what would change it.

``explain(decision, facts, policy, rules, signals)`` never decides anything. It reads
the decision and writes, for the customer:

- ``message``: one sentence that always names the amount, leads with the outcome
  ("Approved" / "Declined" / "Waiting for you") and says why in the deciding rule's or
  signal's own words (E1, E2, E4);
- ``counterfactual``: what would make it a yes, from the deciding rule or signal (E3);
- ``evidence``: every rule result and every triggered signal, plus info rows (E7);
- ``injection_flag``: set when shop text tried to instruct the agent (E5).

Shop text reaches the customer only through rule and signal details, and every such
string is cleaned first: anything that reads as an instruction to the agent is replaced,
so an injected sentence is never repeated (E5). Templates keyed by reason code
(docs/api-contract.md §4) cover the codes that have no deciding detail.
"""

from __future__ import annotations

import ast
import re

from oneguard.engine.facts import CONTRADICTORY
from oneguard.engine.interfaces import register
from oneguard.engine.policy import RESERVATION_ONLY
from oneguard.engine.protections import (
    AGENT_DIRECTED_PATTERNS,
    FLAGGED_SHOP_DETAIL,
    INSTRUCTIONS_IGNORED,
)
from oneguard.engine.types import (
    EngineDecision,
    EvidenceRow,
    EvidenceSource,
    Explanation,
    Facts,
    Policy,
    RuleResult,
    Signal,
)

REMOVED = "[instructions removed]"
LEADS = {"approve": "Approved", "decline": "Declined", "step_up": "Waiting for you"}

# One phrase per reason code (docs/api-contract.md §4). Used when the decision has no
# deciding rule or signal whose own detail says it better.
REASON_TEMPLATES: dict[str, str] = {
    "within_limits": "it is within the limits you set",
    "rule_satisfied": "it meets every rule you set",
    "per_order_limit_exceeded": "it is over your per-order limit",
    "period_limit_exceeded": "it would take you over your spending limit for the period",
    "merchant_category_mismatch": "the shop is not the type of shop you asked for",
    "unfamiliar_merchant": "you have not bought from this shop before",
    "lookalike_merchant": "the shop's name imitates a shop you know",
    "item_mismatch": "it is not the item you asked for",
    "unrequested_item": "the cart has something you did not ask for",
    "return_terms_unknown": "the shop does not state its return terms",
    "return_window_too_short": "the return window is shorter than you asked for",
    "duplicate_suspected": "it looks like a repeat of an order placed shortly before",
    "injection_suspected": "the shop's text contains instructions aimed at the agent, which were ignored",
    "new_device_burst": "the way it was made suggests someone else may be driving",
    "card_or_authority_inactive": "the card or your permission is not active",
    "unevaluable": "not everything could be checked in time",
    "customer_confirmation": "you confirmed it",
    "split_order_suspected": "together with an order a few minutes earlier it goes over your per-order limit",
    "requote_accepted": "it is a new quote after an earlier decline and meets your rules",
    "already_fulfilled": "you already have what you asked for",
    "recurring_charge_added": "it adds a recurring charge you did not ask for",
    "wrong_size": "it is not the size you asked for",
    "session_recovered": "your usual device is back and nothing else looks unusual",
    "on_other_card": "you know this shop from another of your cards",
    "foreign_currency_converted": "it was converted to CHF at the fixed rate",
    "ledger_mismatch": "the platform's spending total differs from ours",
    "period_reserved_pending": "an order still waiting for your answer would take you over your period limit",
    "shop_terms_contradictory": "the shop's description contradicts itself",
    "stub": "the decision engine is not connected yet",
}

SIGNAL_LABELS = {
    "A1": "Instructions in shop text", "A2": "Amount from the payment request",
    "A3": "Repeat order", "A4": "Split order", "A5": "New quote after a decline",
    "A6": "Recurring charge", "A7": "Lookalike shop", "W1": "New device", "W2": "Many attempts",
    "W3": "New country", "W4": "Unusually large amount", "W5": "Night-time",
    "W6": "Unusual price", "S_agent_directed": "Shop text addressed to the agent",
}  # fmt: skip
RULE_LABELS = {
    "policy_status": "Policy active", "authority_status": "Permission active",
    "card_status": "Card active", "C2": "Spending over the period", "C3": "Allowed item types",
    "C4": "Excluded item types", "C5": "The item you asked for", "C8": "Type of shop",
    "C9": "A shop you have bought from", "C10": "Nothing extra",
}  # fmt: skip
SIGNAL_COUNTERFACTUALS = {
    "A1": "Would approve without the instructions in the shop's text.",
    "A3": "Would approve if it is not a repeat of the earlier order.",
    "A4": "Would approve if the two orders together stay within your per-order limit.",
    "A6": "Would approve without the recurring add-on.",
    "A7": "Would approve at the shop you know.",
}
_RULE_OUTCOME = {"pass": "pass", "fail": "fail", "unknown": "uncertain"}
_SIGNAL_OUTCOME = {"decline": "fail", "ask": "uncertain", "info": "info"}
_FACT_SOURCE: dict[str, EvidenceSource] = {
    "event": "policy", "regex": "merchant_text", "model": "model", "history": "history",
}  # fmt: skip
_SETTING = {"ask": "ask you", "decline": "decline", "approve": "approve"}


# facts.py marks a self-contradicting shop text as "contradictory <topic> in shop text: [values]".
_CONTRADICTION = re.compile(rf"{CONTRADICTORY} (?P<topic>[a-z ]+?) in shop text: (?P<values>\[[^\]]*\])")
_TOPICS = {"return terms": "returns", "sizes": "size"}


def _term(topic: str, value: object) -> str:
    if topic == "returns" and isinstance(value, int):
        return "final sale" if value == 0 else f"{value} days"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _contradiction_phrase(match: re.Match[str]) -> str:
    """"the shop's description contradicts itself about returns (30 days and final sale)"."""
    topic = _TOPICS.get(match["topic"], match["topic"])
    try:
        values = ast.literal_eval(match["values"])
    except (ValueError, SyntaxError):
        return f"the shop's description contradicts itself about {topic}"
    ordered = sorted(values, key=lambda v: (v == 0, str(v) == "exchange only", v if isinstance(v, int | float) else 0))
    return f"the shop's description contradicts itself about {topic} ({_and([_term(topic, v) for v in ordered])})"


def contradiction(facts: Facts) -> str | None:
    """The shop's self-contradiction, in the customer's words, from the first fact marked
    contradictory (order return window, then each line's return window and sizes)."""
    candidates = [facts.return_window_days]
    for line in facts.items:
        candidates += [line.return_window_days, line.size_eu, line.size_letter]
    for fact in candidates:
        if fact.detail.startswith(CONTRADICTORY) and (m := _CONTRADICTION.search(fact.detail)):
            return _contradiction_phrase(m)
    return None


def clean(text: str | None, facts: Facts) -> str:
    """Shop-derived text with every instruction to the agent removed (E5), and internal
    markers (M5 reservation, raw contradiction lists) turned into plain words."""
    if not text:
        return ""
    text = text.replace(f"; {RESERVATION_ONLY}", "").replace(RESERVATION_ONLY, "")
    text = _CONTRADICTION.sub(_contradiction_phrase, text)
    for span in facts.agent_directed_text:
        text = re.sub(re.escape(span), REMOVED, text, flags=re.IGNORECASE)
    for pattern in AGENT_DIRECTED_PATTERNS:
        text = pattern.sub(REMOVED, text)
    return text.strip()


_MID_SENTENCE_WORDS = ("You ", "The ", "This ", "It ", "Your ")


def _clause(text: str) -> str:
    """One clause of a sentence: inner full stops become semicolons, no final stop."""
    text = re.sub(r"\.\s+", "; ", text.strip()).rstrip(".; ")
    for word in _MID_SENTENCE_WORDS:
        text = text.replace(f"; {word}", f"; {word[0].lower()}{word[1:]}")
    return text


def _first_sentence(text: str) -> str:
    """A signal's headline: its first sentence (the rest stays in the evidence)."""
    return re.split(r"(?<=\.)\s+(?=[A-Z])", text.strip(), maxsplit=1)[0]


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def _rule_label(result: RuleResult, policy: Policy) -> str:
    for rule in policy.rules:
        if rule.id == result.rule_id and rule.text:
            return rule.text
    return RULE_LABELS.get(result.rule_id, result.rule_id)


def _evidence(
    facts: Facts, policy: Policy, rules: list[RuleResult], signals: list[Signal], decision: EngineDecision
) -> list[EvidenceRow]:
    rows = [
        EvidenceRow(
            rule=_rule_label(r, policy), outcome=_RULE_OUTCOME[r.outcome],
            detail=clean(r.detail, facts), source=_FACT_SOURCE.get(r.source, "policy"),
        )
        for r in rules
    ]
    for s in signals:
        if s.triggered:
            rows.append(EvidenceRow(
                rule=SIGNAL_LABELS.get(s.id, s.id), outcome=_SIGNAL_OUTCOME[s.outcome_if_triggered],
                detail=clean(s.detail, facts), source=s.source,
            ))  # fmt: skip
        elif s.id == "A1" and s.detail == FLAGGED_SHOP_DETAIL:
            rows.append(EvidenceRow(rule=SIGNAL_LABELS["A1"], outcome="info", detail=s.detail, source=s.source))
    if decision.outcome == "step_up" and any(r.outcome == "unknown" for r in rules):
        rows.append(EvidenceRow(
            rule="When unsure", outcome="info", source="policy",
            detail=f"You asked us to {_SETTING[policy.uncertainty_policy]} when something cannot be checked.",
        ))  # fmt: skip
    if not rows:  # a decision with no evidence is a bug (CLAUDE.md rule 10); never send one
        rows.append(EvidenceRow(rule="Decision", outcome="info", source="policy",
                                detail=_template(decision)))  # fmt: skip
    return rows


def _template(decision: EngineDecision) -> str:
    for code in decision.reason_codes:
        if code in REASON_TEMPLATES:
            return REASON_TEMPLATES[code]
    return {"approve": REASON_TEMPLATES["rule_satisfied"], "decline": "it breaks a rule you set",
            "step_up": "something could not be checked"}[decision.outcome]  # fmt: skip


def _deciding(
    decision: EngineDecision, rules: list[RuleResult], signals: list[Signal]
) -> list[RuleResult | Signal]:
    """The rule results and triggered signals behind the decision, most important first."""
    by_id: dict[str, RuleResult | Signal] = {r.rule_id: r for r in rules}
    by_id.update({s.id: s for s in signals if s.triggered})
    named = [by_id[i] for i in decision.deciding_ids if i in by_id]
    if named:
        return named
    if decision.outcome == "decline":
        found: list[RuleResult | Signal] = [r for r in rules if r.outcome == "fail"]
        return found or [s for s in signals if s.triggered and s.outcome_if_triggered == "decline"]
    if decision.outcome == "step_up":
        found = [r for r in rules if r.outcome == "unknown"]
        return found or [s for s in signals if s.triggered and s.outcome_if_triggered == "ask"]
    return []


def _injection_found(signals: list[Signal]) -> bool:
    return any(s.triggered and s.id in ("A1", "S_agent_directed") for s in signals)


def _message(
    decision: EngineDecision, facts: Facts, deciding: list[RuleResult | Signal], signals: list[Signal]
) -> str:
    amount = f"CHF {facts.billing_amount_chf:.2f}"
    lead = LEADS[decision.outcome]
    if decision.outcome == "approve":
        body = _template(decision)
        requote = next((s for s in signals if s.triggered and s.id == "A5"), None)
        if requote and requote.related:
            body = f"{body}; it is a new quote after the declined {requote.related[0]}"
    elif "shop_terms_contradictory" in decision.reason_codes or (
        deciding and isinstance(deciding[0], RuleResult) and CONTRADICTORY in deciding[0].detail
    ):
        phrase = contradiction(facts) or REASON_TEMPLATES["shop_terms_contradictory"]
        body = phrase[0].upper() + phrase[1:]  # "The shop's description contradicts itself about …"
    elif deciding:
        first = deciding[0]
        if isinstance(first, Signal) and first.id in ("A1", "S_agent_directed"):
            body = INSTRUCTIONS_IGNORED.rstrip(".") + ", so you decide"
        elif isinstance(first, Signal) and first.strength in ("strong", "weak"):
            signs = [d for d in deciding if isinstance(d, Signal) and d.strength in ("strong", "weak")]
            body = "; ".join(_clause(_first_sentence(clean(s.detail, facts))).lower() for s in signs)
        elif isinstance(first, Signal):
            body = _clause(_first_sentence(clean(first.detail, facts))) or _template(decision)
        else:
            body = _clause(clean(first.detail, facts)) or _template(decision)
    else:
        body = _template(decision)
    also_injected = _injection_found(signals) and not (
        deciding and isinstance(deciding[0], Signal) and deciding[0].id in ("A1", "S_agent_directed")
    )
    if also_injected and decision.outcome != "approve":
        body = f"{body}; the shop's text also contained instructions aimed at the agent, which were ignored"
    return _sentence(f"{lead} {amount}: {_clause(body)}")


def _counterfactual(
    decision: EngineDecision, facts: Facts, deciding: list[RuleResult | Signal]
) -> str | None:
    if decision.outcome == "approve" or not deciding:
        return None
    first = deciding[0]
    if isinstance(first, RuleResult):
        return _sentence(clean(first.counterfactual, facts)) or None
    return SIGNAL_COUNTERFACTUALS.get(first.id)


@register("explain")
def explain(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    rules: list[RuleResult],
    signals: list[Signal],
) -> Explanation:
    deciding = _deciding(decision, rules, signals)
    return Explanation(
        message=_message(decision, facts, deciding, signals),
        counterfactual=_counterfactual(decision, facts, deciding),
        evidence=_evidence(facts, policy, rules, signals, decision),
        injection_flag={"flagged": True, "reason": INSTRUCTIONS_IGNORED} if _injection_found(signals) else None,
        source="template",
    )
