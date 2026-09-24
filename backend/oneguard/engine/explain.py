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
    shop_texts,
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
    "rule_not_met": "it breaks a rule you set",
    "unusual_activity": "more than one thing about how it was made is unusual",
    "session_watch": "after the recent burst of unusual attempts on this card, we check with you until you approve a purchase",
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


def injected_spans(facts: Facts) -> list[str]:
    """Every piece of the shop's own text that reads as an instruction to the agent,
    longest first. Only these are removed: the engine's own wording ("Would approve with
    order total …") is never mistaken for an injection."""
    spans = set(facts.agent_directed_text)
    for _, text in shop_texts(facts):
        for pattern in AGENT_DIRECTED_PATTERNS:
            spans.update(m.group(0).lstrip(" \t\n.;:!") for m in pattern.finditer(text or ""))
    return sorted(filter(None, spans), key=len, reverse=True)


def clean(text: str | None, facts: Facts) -> str:
    """Shop-derived text with every instruction to the agent removed (E5), and internal
    markers (M5 reservation, raw contradiction lists) turned into plain words."""
    if not text:
        return ""
    text = text.replace(f"; {RESERVATION_ONLY}", "").replace(RESERVATION_ONLY, "")
    text = _CONTRADICTION.sub(_contradiction_phrase, text)
    for span in injected_spans(facts):
        text = re.sub(re.escape(span), REMOVED, text, flags=re.IGNORECASE)
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
    """The rule results and triggered signals behind the decision, in decide's order.

    When the decision names its deciding ids, only those decide: an id with no result of
    its own (``session_watch``) leaves the reason to the reason-code template, never to a
    signal that did not decide. Without ids (stubs), the failing or unknown rules decide.
    """
    by_id: dict[str, RuleResult | Signal] = {r.rule_id: r for r in rules}
    by_id.update({s.id: s for s in signals if s.triggered})
    if decision.deciding_ids:
        return [by_id[i] for i in decision.deciding_ids if i in by_id]
    if decision.outcome == "decline":
        found: list[RuleResult | Signal] = [r for r in rules if r.outcome == "fail"]
        return found or [s for s in signals if s.triggered and s.outcome_if_triggered == "decline"]
    if decision.outcome == "step_up":
        found = [r for r in rules if r.outcome == "unknown"]
        return found or [s for s in signals if s.triggered and s.outcome_if_triggered == "ask"]
    return []


def _supporting(
    decision: EngineDecision, deciding: list[RuleResult | Signal], signals: list[Signal]
) -> list[Signal]:
    """Triggered signals that did not decide but would have asked on their own (a
    protection, a strong sign, two weak signs): named after the deciding reason."""
    if decision.outcome == "approve":
        return []
    named = {d.id for d in deciding if isinstance(d, Signal)}
    weak = [s for s in signals if s.triggered and s.strength == "weak"]
    return [
        s for s in signals
        if s.triggered and s.id not in named and s.id not in _INJECTION_IDS
        and s.outcome_if_triggered != "info" and (s.strength != "weak" or len(weak) >= 2)
    ]  # fmt: skip


_INJECTION_IDS = ("A1", "S_agent_directed")


def _injection_found(signals: list[Signal]) -> bool:
    return any(s.triggered and s.id in _INJECTION_IDS for s in signals)


def _lower_first(text: str) -> str:
    """Mid-sentence casing: "Made from …" → "made from …"; "CHF 520.00 …" stays."""
    return text[0].lower() + text[1:] if re.match(r"[A-Z][a-z]", text) else text


def _capitalised(phrase: str) -> str:
    return phrase[0].upper() + phrase[1:]


def _reason(item: RuleResult | Signal, decision: EngineDecision, facts: Facts) -> str:
    """One deciding (or supporting) rule or signal, in its own words."""
    if isinstance(item, RuleResult):
        if CONTRADICTORY in item.detail:  # "The shop's description contradicts itself about …"
            m = _CONTRADICTION.search(item.detail)
            phrase = _contradiction_phrase(m) if m else contradiction(facts)
            return _capitalised(phrase or REASON_TEMPLATES["shop_terms_contradictory"])
        return _clause(clean(item.detail, facts))
    if item.id in _INJECTION_IDS:
        ignored = INSTRUCTIONS_IGNORED.rstrip(".")
        return f"{ignored}, so you decide" if decision.outcome == "step_up" else ignored
    return _clause(_first_sentence(clean(item.detail, facts)))


def _message(
    decision: EngineDecision,
    facts: Facts,
    deciding: list[RuleResult | Signal],
    signals: list[Signal],
    counterfactual: str | None,
) -> str:
    """Lead with the deciding reason, then the supporting signals, then (decline only)
    what would make it a yes. Only a step-up invites the customer to decide."""
    amount = f"CHF {facts.billing_amount_chf:.2f}"
    lead = LEADS[decision.outcome]
    if decision.outcome == "approve":
        body = _template(decision)
        requote = next((s for s in signals if s.triggered and s.id == "A5"), None)
        if requote and requote.related:
            body = f"{body}; it is a new quote after the declined {requote.related[0]}"
        return _sentence(f"{lead} {amount}: {_clause(body)}")

    reasons = [r for r in (_reason(d, decision, facts) for d in deciding) if r]
    if not reasons and "shop_terms_contradictory" in decision.reason_codes:
        reasons = [_capitalised(contradiction(facts) or REASON_TEMPLATES["shop_terms_contradictory"])]
    reasons = reasons or [_template(decision)]
    if deciding and isinstance(deciding[0], Signal) and deciding[0].strength in ("strong", "weak"):
        reasons[0] = _lower_first(reasons[0])  # "Waiting for you CHF 165.00: made from a device …"
    supporting = [_lower_first(r) for s in _supporting(decision, deciding, signals) if (r := _reason(s, decision, facts))]
    if supporting:
        reasons.append(f"also {_and(supporting)}")
    if _injection_found(signals) and not any(isinstance(d, Signal) and d.id in _INJECTION_IDS for d in deciding):
        reasons.append("the shop's text also contained instructions aimed at the agent, which were ignored")
    if decision.outcome == "decline" and counterfactual:
        reasons.append(_clause(counterfactual))
    body = "; ".join([reasons[0], *(_lower_first(r) for r in reasons[1:])])
    return _sentence(f"{lead} {amount}: {_clause(body)}")


_WOULD = re.compile(r"^would\s+(\w+)\s+", re.IGNORECASE)


def _joined(counterfactuals: list[str]) -> str | None:
    """"Would approve with A" + "Would approve without B" → "Would approve with A and without B"."""
    texts = list(dict.fromkeys(_clause(t) for t in counterfactuals if t and t.strip()))
    if not texts:
        return None
    verb = m[1].lower() if (m := _WOULD.match(texts[0])) else None
    tails = []
    for text in texts[1:]:
        same = (n := _WOULD.match(text)) is not None and n[1].lower() == verb
        tails.append(text[n.end():] if same and n else _lower_first(text))
    return _sentence(_and([texts[0], *tails]))


def _counterfactual(
    decision: EngineDecision,
    facts: Facts,
    rules: list[RuleResult],
    signals: list[Signal],
    deciding: list[RuleResult | Signal],
) -> str | None:
    """E3. A decline: every failing rule's own counterfactual, joined with "and"; with no
    failing rule, the declining protections'. Never an evidence-only signal. A step-up:
    the deciding rule's or signal's."""
    if decision.outcome == "decline":
        failing = [clean(r.counterfactual, facts) for r in rules if r.outcome == "fail" and r.counterfactual]
        if failing:
            return _joined(failing)
        return _joined([
            SIGNAL_COUNTERFACTUALS.get(s.id, "") for s in signals
            if s.triggered and s.strength == "protection" and s.outcome_if_triggered == "decline"
        ])  # fmt: skip
    if decision.outcome == "step_up" and deciding:
        first = deciding[0]
        if isinstance(first, RuleResult):
            return _sentence(clean(first.counterfactual, facts)) or None
        return SIGNAL_COUNTERFACTUALS.get(first.id)
    return None


@register("explain")
def explain(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    rules: list[RuleResult],
    signals: list[Signal],
) -> Explanation:
    deciding = _deciding(decision, rules, signals)
    counterfactual = _counterfactual(decision, facts, rules, signals, deciding)
    return Explanation(
        message=_message(decision, facts, deciding, signals, counterfactual),
        counterfactual=counterfactual,
        evidence=_evidence(facts, policy, rules, signals, decision),
        injection_flag={"flagged": True, "reason": INSTRUCTIONS_IGNORED} if _injection_found(signals) else None,
        source="template",
    )
