"""decide: turn rule results and signals into approve / decline / ask (lane P2).

rules.md §4, in order; the first step that applies decides:

  1  policy, authority or card not active                       -> decline
  2  a customer rule fails (D1: even under "ask me when unsure") -> decline
     except M5: the period limit fails only because of pending
     reservations                                                -> ask (step 4)
  3  a protection that declines is triggered                    -> decline
  4  a customer rule is unknown -> the uncertainty setting (C11)
     (ask / decline; "approve" lets steps 5-6 still ask, D2)
  5  a protection that asks is triggered                        -> ask
  6  warning signs (W-rules 1-2), a soft signal, or the session
     watch after an attack (PM decision, api-contract §3.6)      -> ask
  7  everything passes, nothing triggered                       -> approve

Principles: missing is never a pass (P3): a period rule or a step-1 check with no result
is unknown, and an unknown step-1 check never approves. The most restrictive result wins
(P4). Signals only add friction (P5): "info" signals never change the outcome.

``session_trust``: ``frozen`` when a new device and a burst come together (W1 + W2),
or while the session watch is on; ``elevated`` for any other strong sign.

Pure function: no I/O. Reason codes use api-contract §4 (new codes requested there:
``rule_not_met``, ``unusual_activity``, ``session_watch``).
"""
from __future__ import annotations

from oneguard.engine.facts import CONTRADICTORY
from oneguard.engine.interfaces import register
from oneguard.engine.policy import CONFIRMED, RESERVATION_ONLY
from oneguard.engine.types import (
    STEP1_RULE_IDS,
    EngineDecision,
    LedgerView,
    Policy,
    Relation,
    Rule,
    RuleResult,
    Signal,
)

SESSION_WATCH = "session_watch"

# Reason codes by what the rule checks (api-contract §4).
_FAIL_CODE_BY_FIELD = {
    "authorization.billing_amount_chf": "per_order_limit_exceeded",
    "items[].item_category": "item_mismatch",
    "items[].size_eu": "wrong_size",
    "items[].size_letter": "wrong_size",
    "order.return_window_days": "return_window_too_short",
    "order.order_returnable": "return_window_too_short",
    "merchant.merchant_category": "merchant_category_mismatch",
    "merchant.familiar_on_card": "unfamiliar_merchant",
    "merchant.known_shop": "unfamiliar_merchant",
    "cart.recurring": "recurring_charge_added",
}
_FAIL_CODE_BY_FLAG = {  # results policy.py reports for Policy flags
    "C3": "item_mismatch",
    "C4": "item_mismatch",
    "C5": "item_mismatch",
    "C8": "merchant_category_mismatch",
    "C9": "unfamiliar_merchant",
    "C10": "unrequested_item",
}
_UNKNOWN_CODE_BY_FIELD = {
    "order.return_window_days": "return_terms_unknown",
    "order.order_returnable": "return_terms_unknown",
    "order.order_cancellable": "return_terms_unknown",
}
_SIGNAL_CODE = {
    "A1": "injection_suspected",
    "A3": "duplicate_suspected",
    "A4": "split_order_suspected",
    "A5": "requote_accepted",
    "A6": "recurring_charge_added",
    "A7": "lookalike_merchant",
    "W1": "new_device_burst",
    "W2": "new_device_burst",
    "S_agent_directed": "injection_suspected",
}
_RELATED_ORDER = ("A3", "A4", "A5")


def _dedupe(codes: list[str]) -> list[str]:
    return list(dict.fromkeys(codes))


def _fail_code(result: RuleResult, rule: Rule | None) -> str:
    if result.rule_id in STEP1_RULE_IDS:
        return "card_or_authority_inactive"
    if rule is not None and rule.scope == "period":
        return "period_limit_exceeded"  # a reservation-only breach never gets here (M5, step 4)
    if rule is not None and rule.field in _FAIL_CODE_BY_FIELD:
        return _FAIL_CODE_BY_FIELD[rule.field]
    return _FAIL_CODE_BY_FLAG.get(result.rule_id, "rule_not_met")


def _unknown_code(result: RuleResult, rule: Rule | None) -> str:
    if CONTRADICTORY in result.detail:
        return "shop_terms_contradictory"
    return _UNKNOWN_CODE_BY_FIELD.get(rule.field or "", "unevaluable") if rule is not None else "unevaluable"


def _signal_code(signal: Signal) -> str:
    return _SIGNAL_CODE.get(signal.id, "unusual_activity")


def _warns(signal: Signal) -> bool:
    """A triggered warning sign that asks; "info" ones (no baseline yet) never do (P5)."""
    return signal.triggered and signal.outcome_if_triggered != "info"


def _session_trust(warnings_: list[Signal], ledger: LedgerView) -> str:
    on = {w.id for w in warnings_ if _warns(w)}
    if {"W1", "W2"} <= on or ledger.frozen:
        return "frozen"
    if any(_warns(w) and w.strength == "strong" for w in warnings_):
        return "elevated"
    return "normal"


def _related(deciding: list[Signal], signals: list[Signal]) -> tuple[str, Relation] | None:
    for s in deciding:
        if s.related is not None:
            return s.related
    for sid in _RELATED_ORDER:
        for s in signals:
            if s.id == sid and s.triggered and s.related is not None:
                return s.related
    return None


@register("decide")
def decide(
    rules: list[RuleResult],
    protections_: list[Signal],
    warnings_: list[Signal],
    soft: list[Signal],
    policy: Policy,
    ledger: LedgerView,
) -> EngineDecision:
    """rules.md §4 steps 1-7 with D1-D2, M5, P3-P5 and the session watch."""
    by_id = {r.id: r for r in policy.rules}
    signals = [*protections_, *warnings_, *soft]
    trust = _session_trust(warnings_, ledger)

    def result(outcome: str, step: int, codes: list[str], ids: list[str],
               deciding: list[Signal] | None = None) -> EngineDecision:
        return EngineDecision(
            outcome=outcome, step=step, reason_codes=_dedupe(codes), deciding_ids=_dedupe(ids),
            related=_related(deciding or [], signals), session_trust=trust,
        )

    step1 = {r.rule_id: r for r in rules if r.rule_id in STEP1_RULE_IDS}
    customer = [r for r in rules if r.rule_id not in STEP1_RULE_IDS]
    # P3: a period rule with no result was not checked, so it is unknown, never a pass.
    reported = {r.rule_id for r in customer}
    customer += [
        RuleResult(rule_id=r.id, outcome="unknown", source="history", detail="period limit not checked")
        for r in policy.rules if r.scope == "period" and r.id not in reported
    ]

    # Step 1: policy, authority, card.
    inactive = [r for r in step1.values() if r.outcome == "fail"]
    if inactive:
        return result("decline", 1, ["card_or_authority_inactive"], [r.rule_id for r in inactive])
    step1_unsure = [rid for rid in STEP1_RULE_IDS if step1.get(rid) is None or step1[rid].outcome != "pass"]

    # Step 2: a broken rule declines, whatever the uncertainty setting (D1).
    reserved_only = [r for r in customer if r.outcome == "fail"
                     and (by_id.get(r.rule_id) is not None and by_id[r.rule_id].scope == "period")
                     and RESERVATION_ONLY in r.detail]
    failed = [r for r in customer if r.outcome == "fail" and r not in reserved_only]
    if failed:
        return result("decline", 2, [_fail_code(r, by_id.get(r.rule_id)) for r in failed],
                      [r.rule_id for r in failed])

    # Step 3: protections that decline.
    blocking = [s for s in protections_ if s.triggered and s.outcome_if_triggered == "decline"]
    if blocking:
        return result("decline", 3, [_signal_code(s) for s in blocking], [s.id for s in blocking], blocking)

    # Step 4: unknowns follow the customer's uncertainty setting (C11); M5 asks.
    unknown = [r for r in customer if r.outcome == "unknown"]
    setting = policy.uncertainty_policy
    unsure_ids = step1_unsure + [r.rule_id for r in unknown]
    unsure_codes = ["unevaluable"] * bool(step1_unsure) + [_unknown_code(r, by_id.get(r.rule_id)) for r in unknown]
    if unsure_ids and setting == "decline":
        return result("decline", 4, unsure_codes, unsure_ids)
    if reserved_only or step1_unsure or (unknown and setting == "ask"):
        codes = ["period_reserved_pending"] * bool(reserved_only) + unsure_codes
        return result("step_up", 4, codes, [r.rule_id for r in reserved_only] + unsure_ids)
    approved_despite_unknown = bool(unknown)  # setting "approve": steps 5-6 may still ask (D2)

    # Step 5: protections that ask.
    asking = [s for s in protections_ if s.triggered and s.outcome_if_triggered == "ask"]
    if asking:
        return result("step_up", 5, [_signal_code(s) for s in asking], [s.id for s in asking], asking)

    # Step 6: warning signs (one strong, or two weak), soft signals, the session watch.
    strong = [w for w in warnings_ if _warns(w) and w.strength == "strong"]
    weak = [w for w in warnings_ if _warns(w) and w.strength == "weak"]
    signs = strong + (weak if len(weak) >= 2 else [])
    signs += [s for s in soft if s.triggered and s.outcome_if_triggered == "ask"]
    if signs:
        return result("step_up", 6, [_signal_code(s) for s in signs], [s.id for s in signs], signs)
    if ledger.frozen:
        return result("step_up", 6, [SESSION_WATCH], [SESSION_WATCH])

    # Step 7 (or step 4 when the customer said to approve when unsure).
    has_limit = any(r.field == "authorization.billing_amount_chf" for r in policy.rules)
    codes = ["within_limits" if has_limit else "rule_satisfied"]
    if any(r.outcome == "pass" and r.detail.startswith(CONFIRMED) for r in customer):
        codes.append("customer_confirmation")
    if any(s.id == "A5" and s.triggered for s in signals):
        codes.append("requote_accepted")
    if approved_despite_unknown:
        return result("approve", 4, [*unsure_codes, *codes], [r.rule_id for r in unknown])
    return result("approve", 7, codes, [])
