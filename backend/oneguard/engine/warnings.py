"""Warning signs W1–W6 (docs/rules.md §8): detection only.

``warning_signs(facts, ledger, policy)`` returns one Signal per sign, triggered or not, with its
strength. decide.py applies the W-rules (one strong → ask; two or more weak → ask; one
weak alone → nothing). Signs are evaluated per purchase and never carry over (W-rule 4).

- W1 new device (strong): device never used by this customer for an approved purchase
- W2 burst (strong): ``recent_attempt_count_10m ≥ 2``
- W3 new country (weak): merchant country never in the customer's approved history
- W4 amount far above normal (weak): total > largest approved purchase, unless it is
  within a stated per-order limit
- W5 night-time (weak): 00:00 ≤ local time < 05:00, Europe/Zurich
- W6 price outside catalogue range (weak): a line's CHF unit price outside the item's
  ``unit_price_min_chf`` … ``unit_price_max_chf``
"""

from __future__ import annotations

from oneguard.engine.interfaces import register
from oneguard.engine.protections import per_order_limit
from oneguard.engine.types import Facts, LedgerView, Policy, Signal

BURST_ATTEMPTS = 2
NIGHT_START_HOUR = 0
NIGHT_END_HOUR = 5


def _sign(sign_id: str, triggered: bool, strength: str, detail: str, source: str) -> Signal:
    return Signal(
        id=sign_id, triggered=triggered, strength=strength, outcome_if_triggered="ask",
        detail=detail, source=source,
    )  # fmt: skip


def w1_new_device(facts: Facts, ledger: LedgerView) -> Signal:
    if not facts.device_id:
        return _sign("W1", True, "strong", "The purchase came without a device id.", "history")
    if facts.device_id in ledger.known_device_ids:
        return _sign("W1", False, "strong", "Made from a device you have used before.", "history")
    return _sign("W1", True, "strong", "Made from a device you have not used before.", "history")


def w2_burst(facts: Facts) -> Signal:
    count = facts.recent_attempt_count_10m
    triggered = count >= BURST_ATTEMPTS
    detail = f"{count} other purchase attempt(s) in the 10 minutes before this one."
    return _sign("W2", triggered, "strong", detail, "ledger")


def w3_new_country(facts: Facts, ledger: LedgerView) -> Signal:
    country = facts.merchant_country
    if country in ledger.known_countries:
        return _sign("W3", False, "weak", f"You have bought from shops in {country} before.", "history")
    return _sign("W3", True, "weak", f"First purchase from a shop in {country}.", "history")


def w4_amount_above_normal(
    facts: Facts, ledger: LedgerView, per_order_limit_chf: float | None = None
) -> Signal:
    total = facts.billing_amount_chf
    largest = ledger.max_approved_chf
    if per_order_limit_chf is not None and total <= per_order_limit_chf:
        detail = f"CHF {total:.2f} is within your CHF {per_order_limit_chf:.2f} per-order limit."
        return _sign("W4", False, "weak", detail, "history")
    if largest is None:
        return _sign("W4", True, "weak", f"CHF {total:.2f}, and there is no earlier approved purchase to compare with.", "history")
    if total > largest:
        detail = f"CHF {total:.2f} is more than your largest approved purchase (CHF {largest:.2f})."
        return _sign("W4", True, "weak", detail, "history")
    detail = f"CHF {total:.2f} is not more than your largest approved purchase (CHF {largest:.2f})."
    return _sign("W4", False, "weak", detail, "history")


def w5_night(facts: Facts) -> Signal:
    hour = facts.local_hour
    triggered = NIGHT_START_HOUR <= hour < NIGHT_END_HOUR
    when = "at night" if triggered else "outside night hours"
    return _sign("W5", triggered, "weak", f"Made {when} ({hour:02d}:xx Zurich time).", "ledger")


def w6_price_outside_range(facts: Facts) -> Signal:
    outside = []
    for line in facts.items:
        low, high = line.unit_price_min_chf, line.unit_price_max_chf
        if low is not None and line.unit_price_chf < low:
            outside.append(f"line {line.line_no} CHF {line.unit_price_chf:.2f} below the usual CHF {low:.2f}")
        elif high is not None and line.unit_price_chf > high:
            outside.append(f"line {line.line_no} CHF {line.unit_price_chf:.2f} above the usual CHF {high:.2f}")
    if outside:
        return _sign("W6", True, "weak", "Unusual price: " + "; ".join(outside) + ".", "history")
    return _sign("W6", False, "weak", "Prices are within the usual range for these items.", "history")


def evaluate(facts: Facts, ledger: LedgerView, per_order_limit_chf: float | None = None) -> list[Signal]:
    """W1–W6. ``per_order_limit_chf`` is the stated per-order limit, which suppresses W4."""
    return [
        w1_new_device(facts, ledger),
        w2_burst(facts),
        w3_new_country(facts, ledger),
        w4_amount_above_normal(facts, ledger, per_order_limit_chf),
        w5_night(facts),
        w6_price_outside_range(facts),
    ]


@register("warning_signs")
def warning_signs(facts: Facts, ledger: LedgerView, policy: Policy) -> list[Signal]:
    limit = per_order_limit(policy)
    return evaluate(facts, ledger, limit[0] if limit else None)
