"""Explanations (docs/rules.md §9, E1–E7): one plain sentence, the evidence, what would change it.

``explain(decision, facts, policy, rules, signals)`` never decides anything. It reads
the decision and writes, for the customer:

- ``message``: one sentence that names the amount once, leads with the outcome
  ("Approved" / "Declined" / "Waiting for you") and says why in the deciding rule's or
  signal's own words (E1, E2, E4); a decline says it in short words and ends with the
  counterfactual as a second sentence, so the suggestion is said once (E3);
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
from decimal import Decimal

from oneguard.engine.facts import CONTRADICTORY, to_chf
from oneguard.engine.interfaces import register
from oneguard.engine.policy import NO_HISTORY, RESERVATION_ONLY
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
    Rule,
    RuleResult,
    Signal,
)

REMOVED = "[instructions removed]"
LEADS = {"approve": "Approved", "decline": "Declined", "step_up": "Waiting for you"}
EXPIRED_MESSAGE = "Expired: no answer within {seconds} s; nothing was approved."
"""rules.md Q2: the stored message of a step-up closed by the timeout."""


def expired_message(window_s: float) -> str:
    """The message a step-up keeps once its human window lapsed unanswered (rules.md Q2),
    naming the configured window (``/v1/bootstrap``, 120 s by default)."""
    seconds = int(window_s) if float(window_s).is_integer() else f"{window_s:g}"
    return EXPIRED_MESSAGE.format(seconds=seconds)


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
    "no_purchase_history": "you have no purchase history yet, so we can't tell whether you know this shop",
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


def _and(parts: list[str], word: str = "and") -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} {word} {parts[-1]}"


def _or(parts: list[str]) -> str:
    return _and(parts, "or")


def _a(noun: str) -> str:
    return f"{'an' if noun[:1].lower() in 'aeiou' else 'a'} {noun}"


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


def _reason(item: RuleResult | Signal, decision: EngineDecision, facts: Facts, policy: Policy) -> str:
    """One deciding (or supporting) rule or signal, in its own words. In a decline the
    reason is already cased for mid-sentence and says neither the amount nor what would
    make it a yes: the lead and the "Would approve" sentence say those once."""
    declined = decision.outcome == "decline"
    if isinstance(item, RuleResult):
        if CONTRADICTORY in item.detail:  # "The shop's description contradicts itself about …"
            m = _CONTRADICTION.search(item.detail)
            phrase = _contradiction_phrase(m) if m else contradiction(facts)
            phrase = phrase or REASON_TEMPLATES["shop_terms_contradictory"]
            return phrase if declined else _capitalised(phrase)
        if declined:
            return _declined_rule(item, _policy_rule(item, policy), facts)
        return _clause(clean(item.detail, facts))
    if item.id in _INJECTION_IDS:
        ignored = INSTRUCTIONS_IGNORED.rstrip(".")
        return f"{ignored}, so you decide" if decision.outcome == "step_up" else _lower_first(ignored)
    text = _clause(_first_sentence(clean(item.detail, facts)))
    return _lower_first(text) if declined else text


# --- a decline, said once ------------------------------------------------------------------
# "Declined CHF 38.90: over your CHF 20.00 per-order limit. Would approve at CHF 20.00 or
# less." The rule result's own detail ("Order total: CHF 38.90. You asked for order total
# at or below CHF 20.00") repeats both the amount and the suggestion, so a decline says the
# broken rule in short words from the customer's rule, and the suggestion only in the
# counterfactual sentence.

_ASKED = ". You asked for "  # policy.py: a typed rule's fail is "<Label>: <seen>. You asked for <want>"
_LIMIT_NOUNS = {"authorization.billing_amount_chf": "per-order", "items[].unit_price_chf": "per-item"}
_KNOWN_SHOP = ("you haven't bought from this shop before", "Would approve at a shop you've bought from before")
_YES_NO_RULES = {  # (field, operator, value): (reason, counterfactual)
    ("merchant.known_shop", "=", "true"): _KNOWN_SHOP,
    ("merchant.familiar_on_card", "=", "true"): _KNOWN_SHOP,
    ("cart.recurring", "=", "false"): ("it adds a recurring charge you did not ask for",
                                        "Would approve without the recurring charge"),
    ("order.order_returnable", "=", "true"): ("the order cannot be returned",
                                               "Would approve if the order can be returned"),
    ("order.order_cancellable", "=", "true"): ("the order cannot be cancelled",
                                                "Would approve if the order can be cancelled"),
}  # fmt: skip
_STEP1_SUBJECTS = {"policy_status": "the policy", "authority_status": "the agent's authority",
                   "card_status": "the card"}  # fmt: skip
_CART = re.compile(r"^Cart (contains|includes) ")
_NOT_REQUESTED = re.compile(r"^Cart contains (?P<names>.+), not the .+ you asked for$")
_OTHER_SHOP_TYPE = re.compile(r"^(?P<merchant>.+) is an? (?P<type>.+?) shop, not [^,]+$")


def _chf(value: float) -> str:
    return f"CHF {Decimal(str(value)):.2f}"


def _policy_rule(result: RuleResult, policy: Policy) -> Rule | None:
    return next((r for r in policy.rules if r.id == result.rule_id), None)


def _limit_chf(rule: Rule) -> float | None:
    """A typed money rule's limit in CHF (T4), or None when it is not a plain number."""
    if isinstance(rule.value, bool) or not isinstance(rule.value, int | float):
        return None
    if not rule.currency or rule.currency == "CHF":
        return float(rule.value)
    try:
        return to_chf(rule.value, rule.currency)
    except ValueError:
        return None


def _values(rule: Rule) -> list[str]:
    """A rule's value(s) as words: sporting_goods -> sporting goods."""
    return [str(v).replace("_", " ") for v in (rule.value if isinstance(rule.value, list) else [rule.value])]


def _typed_fail(result: RuleResult, rule: Rule | None, facts: Facts) -> tuple[str, str] | None:
    """A broken typed rule as (short reason, counterfactual), or None to keep its own words."""
    if rule is None or result.outcome != "fail" or not rule.field or rule.scope == "period":
        return None
    field, op = rule.field, rule.operator or ""
    if field in _LIMIT_NOUNS and op in ("<", "<=") and (limit := _limit_chf(rule)) is not None:
        cap = _chf(limit)
        broken = f"{'over' if op == '<=' else 'not under'} your {cap} {_LIMIT_NOUNS[field]} limit"
        within = f"at {cap} or less" if op == "<=" else f"under {cap}"
        if field == "items[].unit_price_chf":
            return f"an item is {broken}", f"Would approve with every item {within}"
        return broken, f"Would approve {within}"
    known = _YES_NO_RULES.get((field, op, str(rule.value).lower()))
    if known:
        return known
    detail = clean(result.detail, facts)
    if _ASKED not in detail or ": " not in detail:
        return None
    label, seen = detail.split(_ASKED, 1)[0].split(": ", 1)
    counterfactual = clean(result.counterfactual, facts)
    if field == "items[].item_category" and op in ("in", "not_in"):
        counterfactual = f"Would approve with only {_or(_values(rule))}" if op == "in" else f"Would approve without {seen}"
    if field == "merchant.merchant_category" and op in ("=", "in"):
        return f"the shop is {_a(seen)} shop", f"Would approve at {_a(_or(_values(rule)))} shop"
    if field == "order.return_window_days" and seen == "no returns":
        return "the shop takes no returns", counterfactual
    return f"the {label.lower()} is {seen}", counterfactual


def _declined_rule(result: RuleResult, rule: Rule | None, facts: Facts) -> str:
    """A deciding rule of a decline, in short words that repeat neither the amount nor
    the suggestion. Rule details the engine writes for a flag (C3-C10) or step 1 keep
    their words, cased for mid-sentence; a merchant name keeps its case."""
    typed = _typed_fail(result, rule, facts)
    if typed:
        return typed[0]
    if result.detail == NO_HISTORY:  # "approve once and I'll remember it" is for a step-up
        return REASON_TEMPLATES["no_purchase_history"]
    detail = _clause(clean(result.detail, facts))
    if result.rule_id in _STEP1_SUBJECTS:  # "Card is blocked"
        return f"{_STEP1_SUBJECTS[result.rule_id]} {detail.split(' ', 1)[1]}"
    if result.rule_id == "C5" and (m := _NOT_REQUESTED.match(detail)):
        return f"the cart has {m['names']}, not what you asked for"
    if result.rule_id == "C8" and (m := _OTHER_SHOP_TYPE.match(detail)):
        return f"{m['merchant']} is {_a(m['type'])} shop"
    if _CART.match(detail):
        return f"the cart {detail[len('Cart '):]}"
    if re.match(r"\d+-day total ", detail):
        return f"the {detail}"
    return _lower_first(detail)


def _amount_once(reason: str, amount: str) -> str:
    """A sign that opens with the purchase amount ("CHF 459.00 is more than …") reads "it
    is more than …": the lead already names the amount."""
    return f"it{reason[len(amount):]}" if reason.startswith(f"{amount} ") else reason


def _message(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    deciding: list[RuleResult | Signal],
    signals: list[Signal],
    counterfactual: str | None,
) -> str:
    """Lead with the amount and the deciding reason, then the supporting signals. A
    decline ends with one sentence on what would make it a yes; only a step-up invites
    the customer to decide."""
    amount = _chf(facts.billing_amount_chf)
    lead = LEADS[decision.outcome]
    if decision.outcome == "approve":
        body = _template(decision)
        requote = next((s for s in signals if s.triggered and s.id == "A5"), None)
        if requote and requote.related:
            body = f"{body}; it is a new quote after the declined {requote.related[0]}"
        return _sentence(f"{lead} {amount}: {_clause(body)}")

    declined = decision.outcome == "decline"
    failed = {d.rule_id for d in deciding if isinstance(d, RuleResult) and d.outcome == "fail"}
    # C10 says again what a broken C5 already said: the cart is not what was asked for.
    shown = [d for d in deciding if not (declined and "C5" in failed and isinstance(d, RuleResult) and d.rule_id == "C10")]
    reasons = [_amount_once(r, amount) for r in (_reason(d, decision, facts, policy) for d in shown) if r]
    if not reasons and "shop_terms_contradictory" in decision.reason_codes:
        phrase = contradiction(facts) or REASON_TEMPLATES["shop_terms_contradictory"]
        reasons = [phrase if declined else _capitalised(phrase)]
    reasons = reasons or [_template(decision)]
    if not declined and deciding and isinstance(deciding[0], Signal) and deciding[0].strength in ("strong", "weak"):
        reasons[0] = _lower_first(reasons[0])  # "Waiting for you CHF 165.00: made from a device …"
    supporting = [
        _amount_once(_lower_first(r), amount)
        for s in _supporting(decision, deciding, signals) if (r := _reason(s, decision, facts, policy))
    ]  # fmt: skip
    if supporting:
        reasons.append(f"also {_and(supporting)}")
    if _injection_found(signals) and not any(isinstance(d, Signal) and d.id in _INJECTION_IDS for d in deciding):
        reasons.append("the shop's text also contained instructions aimed at the agent, which were ignored")
    rest = reasons[1:] if declined else [_lower_first(r) for r in reasons[1:]]
    message = _sentence(f"{lead} {amount}: {_clause('; '.join([reasons[0], *rest]))}")
    return f"{message} {counterfactual}" if declined and counterfactual else message


_WOULD = re.compile(r"^would\s+(\w+)\s+", re.IGNORECASE)


def _joined(counterfactuals: list[str]) -> str | None:
    """"Would approve with A" + "Would approve without B" → "Would approve with A and without B";
    a limit with no room left follows with "but": "…, but nothing more fits in this 7-day window"."""
    texts = list(dict.fromkeys(_clause(t) for t in counterfactuals if t and t.strip()))
    if not texts:
        return None
    verb = m[1].lower() if (m := _WOULD.match(texts[0])) else None
    tails, buts = [], []
    for text in texts[1:]:
        same = (n := _WOULD.match(text)) is not None and n[1].lower() == verb
        if same and n:
            tails.append(text[n.end():])
        else:
            buts.append(_lower_first(text))
    joined = _and([texts[0], *tails])
    return _sentence(f"{joined}, but {_and(buts)}" if buts else joined)


def _rule_counterfactual(result: RuleResult, policy: Policy, facts: Facts) -> str:
    """A broken rule's counterfactual: the short one for a typed rule ("Would approve at
    CHF 20.00 or less"), else the rule's own."""
    typed = _typed_fail(result, _policy_rule(result, policy), facts)
    return typed[1] if typed and typed[1] else clean(result.counterfactual, facts)


def _counterfactual(
    decision: EngineDecision,
    facts: Facts,
    policy: Policy,
    rules: list[RuleResult],
    signals: list[Signal],
    deciding: list[RuleResult | Signal],
) -> str | None:
    """E3. A decline: every failing rule's own counterfactual, joined with "and"; with no
    failing rule, the declining protections'. Never an evidence-only signal. A step-up:
    the deciding rule's or signal's."""
    if decision.outcome == "decline":
        failing = [cf for r in rules if r.outcome == "fail" and (cf := _rule_counterfactual(r, policy, facts))]
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
    counterfactual = _counterfactual(decision, facts, policy, rules, signals, deciding)
    return Explanation(
        message=_message(decision, facts, policy, deciding, signals, counterfactual),
        counterfactual=counterfactual,
        evidence=_evidence(facts, policy, rules, signals, decision),
        injection_flag={"flagged": True, "reason": INSTRUCTIONS_IGNORED} if _injection_found(signals) else None,
        source="template",
    )
