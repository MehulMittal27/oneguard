"""Answer key for the four unseen instructions (lane P2).

Each instruction is given as the policy we expect the compiler to produce, then a set of
purchases with (a) the expected result of every rule and (b) the expected final outcome.

(a) is asserted now against facts.py + policy.py.
(b) is asserted against the whole engine core: each instruction's purchases run in order
    through facts, policy, a StoreLedger and decide, with the customer's scripted answers.
    It includes the protections (A3, injected: P5's lane) and "ask once, then remember".

PM decisions baked in (24 Sep):
- "two tickets" = total across the cart (1 line x 2 or 2 lines x 1).
- "arrive by Friday" = the Friday after the instruction was confirmed (Mon 10 Aug 2026 -> Fri 14 Aug).
- "same price as last time, ask me if anything changed" -> a different price ASKS (rule kind "ask").
- A restriction with no data ("official ticket seller", "the present I picked") asks once;
  when the customer approves, the answer is remembered for that shop (and item) under this policy.
- No invented limits (T2): no amount stated -> no amount rule, only an open question.

Synthetic data: shops and items come from the data pack where it has them; the gym and the
ticket items are invented (the pack has no gym and no concert tickets).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from oneguard.engine.facts import build_facts
from oneguard.engine.policy import evaluate_rules
from oneguard.engine.types import STEP1_RULE_IDS, LedgerView, Policy, Rule

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
# "ask me if anything changed" needs Rule.on_fail (P2 contract request).
HAS_ON_FAIL = "on_fail" in Rule.model_fields
# "ask once, then remember" needs LedgerView.confirmed_keys (P2 contract request).
HAS_CONFIRMED_KEYS = "confirmed_keys" in LedgerView.model_fields
ASK = {"on_fail": "ask"} if HAS_ON_FAIL else {}


def _policy(**kw) -> Policy:
    return Policy(status="active", uncertainty_policy="ask", **kw)

# --- The four compiled policies (what P4's compiler should produce) -------------------------
POLICIES: dict[str, Policy] = {
    "tickets": _policy(
        mandate_id="U-TICKETS",
        instruction="Buy two concert tickets, max CHF 90 each, from the official ticket seller",
        requested_item="concert tickets",
        rules=[
            Rule(id="C12-qty", field="cart.quantity", operator="=", value=2, text="two", source="exact"),
            Rule(id="C12-price", field="items[].unit_price_chf", operator="<=", value=90, currency="CHF",
                 scope="purchase", text="max CHF 90 each", source="exact"),
            # No data can check this: a field outside the vocabulary -> always unknown.
            Rule(id="U1", field="unverifiable", operator="=", value="from the official ticket seller",
                 text="from the official ticket seller", source="exact"),  # ask once, then remember
        ],
    ),
    "lunch": _policy(
        mandate_id="U-LUNCH",
        instruction="Order lunch on weekdays only, under CHF 25",
        rules=[
            Rule(id="C1", field="authorization.billing_amount_chf", operator="<", value=25, currency="CHF",
                 scope="purchase", text="under CHF 25", source="exact"),
            Rule(id="C3", field="items[].item_category", operator="in", value=["dining", "food_delivery"],
                 source="inferred", text="lunch"),
            Rule(id="C12-day", field="authorization.weekday", operator="in", value=WEEKDAYS, text="on weekdays only",
                 source="exact"),
        ],
    ),
    "present": _policy(
        mandate_id="U-PRESENT",
        instruction="Buy the birthday present I picked from a Swiss shop, must arrive by Friday",
        rules=[
            Rule(id="U2", field="unverifiable", operator="=", value="the birthday present I picked",
                 text="the birthday present I picked", source="exact"),  # ask once, then remember
            Rule(id="C12-country", field="merchant.merchant_country", operator="=", value="CH",
                 text="from a Swiss shop", source="exact"),
            Rule(id="C12-delivery", field="authorization.delivery_by", operator="<=", value="2026-08-14",
                 text="must arrive by Friday", source="exact"),  # confirmed Mon 10 Aug -> Fri 14 Aug
        ],
    ),
    "gym": _policy(
        mandate_id="U-GYM",
        instruction="Renew my gym membership, same price as last time, ask me if anything changed",
        requested_item="gym membership",
        rules=[
            Rule(id="C3", field="items[].item_category", operator="in", value=["membership"],
                 source="inferred", text="gym membership"),
            # Last approved price at the customer's gym (CHF 59.00, 12 Jul), resolved by the
            # compiler from history. "ask me if anything changed" -> kind ask.
            Rule(id="C1-same", field="authorization.billing_amount_chf", operator="=", value=59.00,
                 currency="CHF", scope="purchase", source="inferred", text="same price as last time", **ASK),
            # "Renew" = the same gym; a different gym is a change -> kind ask.
            Rule(id="C9-same", field="merchant.familiar_on_card", operator="=", value="true",
                 source="inferred", text="renew (same gym)", **ASK),
        ],
    ),
}

OPEN_QUESTIONS = {
    "tickets": ["No order limit stated: is it CHF 180 (two tickets at CHF 90)?", "Which shop is the official seller?"],
    "lunch": [],
    "present": ["No amount stated: what is the most this present may cost?"],
    "gym": [],
}

# Shops: data-pack ids where they exist; invented ones are prefixed ME_T.
EVENTFORGE = ("ME0048", "EventForge", "entertainment", "CH")
SCREENGARDEN = ("ME0049", "ScreenGarden", "entertainment", "CH")
NOON_SPOON = ("ME0013", "Noon Spoon", "dining", "CH")
MEALWING = ("ME0015", "MealWing", "food_delivery", "CH")
RIVERSIDE = ("ME0014", "Riverside Plate", "dining", "DE")
LOOM = ("ME0025", "Loom and Pine", "clothing", "CH")
MILANO = ("ME0027", "Milano Weave", "clothing", "IT")
PIXELHARBOR = ("ME0022", "PixelHarbor", "electronics", "CH")
ALPINE_GYM = ("ME_T_GYM1", "Alpine Fitness Club", "health", "CH")
LAKESIDE_GYM = ("ME_T_GYM2", "Lakeside Gym", "health", "CH")

TICKET = ("IT_T_TICKET", "Concert ticket", "entertainment")
TOUR = ("IT_T_TOUR", "Stadium tour", "entertainment")
LUNCH = ("IT0030", "Lunch order", "dining")
DELIVERED_LUNCH = ("IT0036", "Prepared lunch order", "food_delivery")
PERFUME = ("IT0062", "Fragrance and beauty gift", "cosmetics")
JACKET = ("IT0049", "Everyday jacket", "clothing")
COAT = ("IT0052", "Rain coat", "clothing")
MONITOR = ("IT0017", "27-inch computer monitor", "electronics")
GYM = ("IT_T_GYM", "Gym membership", "membership")
TOWEL = ("IT_T_TOWEL", "Towel service", "membership")
MUSIC = ("IT0040", "Music plan", "subscriptions")


@dataclass
class Case:
    id: str
    instruction: str
    when: str                      # UTC; Zurich is UTC+2 in August
    shop: tuple
    lines: list                    # (item, qty, unit_price)
    rules: dict                    # rule id -> expected pass | fail | unknown
    final: str                     # expected final outcome: approve | decline | step_up
    why: str
    currency: str = "CHF"
    delivery_by: str | None = None
    known_shop: bool | None = None
    memory: str = ""               # remembered confirmations in force before this purchase
    customer: str = ""             # scripted answer when final == step_up
    remember: str = ""             # what an approve adds to memory
    extras: dict = field(default_factory=dict)


CASES: list[Case] = [
    # ---- Tickets: "Buy two concert tickets, max CHF 90 each, from the official ticket seller"
    Case("T1", "tickets", "2026-08-11T10:00:00Z", EVENTFORGE, [(TICKET, 2, 85.0)],
         {"C12-qty": "pass", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "step_up",
         "Everything checks out except 'official seller', which no data can confirm: ask once",
         customer="approve", remember="U1 official seller = EventForge"),
    Case("T2", "tickets", "2026-08-13T10:00:00Z", EVENTFORGE, [(TICKET, 1, 89.0), (TICKET, 1, 89.0)],
         {"C12-qty": "pass", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "approve",
         "Two lines of 1 = two tickets (count the total); seller confirmed in T1",
         memory="U1 official seller = EventForge"),
    Case("T3", "tickets", "2026-08-14T10:00:00Z", EVENTFORGE, [(TICKET, 3, 80.0)],
         {"C12-qty": "fail", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "decline",
         "Three tickets, not two", memory="U1 official seller = EventForge"),
    Case("T4", "tickets", "2026-08-14T12:00:00Z", EVENTFORGE, [(TICKET, 1, 80.0)],
         {"C12-qty": "fail", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "decline",
         "One ticket, not two", memory="U1 official seller = EventForge"),
    Case("T5", "tickets", "2026-08-15T10:00:00Z", EVENTFORGE, [(TICKET, 2, 90.0)],
         {"C12-qty": "pass", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "approve",
         "CHF 90.00 each: 'max' means equal passes", memory="U1 official seller = EventForge"),
    Case("T6", "tickets", "2026-08-16T10:00:00Z", EVENTFORGE, [(TICKET, 2, 95.0)],
         {"C12-qty": "pass", "C12-price": "fail", "U1": "unknown", "C5": "pass"}, "decline",
         "CHF 95.00 each, over the CHF 90 per-ticket limit", memory="U1 official seller = EventForge"),
    Case("T7", "tickets", "2026-08-17T10:00:00Z", EVENTFORGE, [(TICKET, 2, 94.0)],
         {"C12-qty": "pass", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "approve",
         "EUR 94.00 = CHF 89.30 each: under the limit after conversion", currency="EUR",
         memory="U1 official seller = EventForge"),
    Case("T8", "tickets", "2026-08-18T10:00:00Z", SCREENGARDEN, [(TICKET, 2, 70.0)],
         {"C12-qty": "pass", "C12-price": "pass", "U1": "unknown", "C5": "pass"}, "step_up",
         "Different shop: the remembered answer covers EventForge only, so ask again",
         memory="U1 official seller = EventForge", customer="decline"),
    Case("T9", "tickets", "2026-08-19T10:00:00Z", EVENTFORGE, [(TOUR, 2, 40.0)],
         {"C12-qty": "fail", "C12-price": "pass", "U1": "unknown", "C5": "fail"}, "decline",
         "Stadium tour, not concert tickets (and zero tickets in the cart)",
         memory="U1 official seller = EventForge"),

    # ---- Lunch: "Order lunch on weekdays only, under CHF 25"
    Case("L1", "lunch", "2026-08-11T10:15:00Z", NOON_SPOON, [(LUNCH, 1, 18.50)],
         {"C1": "pass", "C3": "pass", "C12-day": "pass"}, "approve", "Tuesday lunch, CHF 18.50"),
    Case("L2", "lunch", "2026-08-12T10:15:00Z", NOON_SPOON, [(LUNCH, 1, 24.99)],
         {"C1": "pass", "C3": "pass", "C12-day": "pass"}, "approve", "CHF 24.99 is under 25"),
    Case("L3", "lunch", "2026-08-13T10:15:00Z", NOON_SPOON, [(LUNCH, 1, 25.00)],
         {"C1": "fail", "C3": "pass", "C12-day": "pass"}, "decline",
         "Exactly CHF 25.00: 'under' means equal fails"),
    Case("L4", "lunch", "2026-08-15T10:00:00Z", NOON_SPOON, [(LUNCH, 1, 18.00)],
         {"C1": "pass", "C3": "pass", "C12-day": "fail"}, "decline", "Saturday"),
    Case("L5", "lunch", "2026-08-14T23:30:00Z", NOON_SPOON, [(LUNCH, 1, 18.00)],
         {"C1": "pass", "C3": "pass", "C12-day": "fail"}, "decline",
         "Friday 23:30 UTC is Saturday 01:30 in Zurich: judged in Zurich time"),
    Case("L6", "lunch", "2026-08-16T22:30:00Z", NOON_SPOON, [(LUNCH, 1, 18.00)],
         {"C1": "pass", "C3": "pass", "C12-day": "pass"}, "approve",
         "Sunday 22:30 UTC is Monday 00:30 in Zurich: a weekday. No lunchtime hours were stated, so none are invented (T2)"),
    Case("L7", "lunch", "2026-08-18T10:00:00Z", MEALWING, [(DELIVERED_LUNCH, 1, 22.00)],
         {"C1": "pass", "C3": "pass", "C12-day": "pass"}, "approve", "Delivered lunch counts as lunch"),
    Case("L8", "lunch", "2026-08-19T10:00:00Z", NOON_SPOON, [(LUNCH, 1, 12.00), (PERFUME, 1, 10.00)],
         {"C1": "pass", "C3": "fail", "C12-day": "pass"}, "decline", "A perfume gift set is not lunch"),
    Case("L9", "lunch", "2026-08-19T11:00:00Z", RIVERSIDE, [(LUNCH, 1, 24.00)],
         {"C1": "pass", "C3": "pass", "C12-day": "pass"}, "approve",
         "EUR 24.00 = CHF 22.80 at a German shop: no country rule was stated", currency="EUR"),
    Case("L10", "lunch", "2026-08-20T11:00:00Z", RIVERSIDE, [(LUNCH, 1, 26.32)],
         {"C1": "fail", "C3": "pass", "C12-day": "pass"}, "decline",
         "EUR 26.32 = CHF 25.00 exactly after conversion: not under 25", currency="EUR"),

    # ---- Present: "Buy the birthday present I picked from a Swiss shop, must arrive by Friday"
    Case("P1", "present", "2026-08-11T09:00:00Z", LOOM, [(JACKET, 1, 145.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "pass"}, "step_up",
         "Swiss shop, arrives Thu 13 Aug; only the customer knows if this is the present they picked",
         delivery_by="2026-08-13", customer="approve", remember="U2 present = Everyday jacket at Loom and Pine"),
    Case("P2", "present", "2026-08-11T11:00:00Z", MILANO, [(JACKET, 1, 150.0)],
         {"U2": "unknown", "C12-country": "fail", "C12-delivery": "pass"}, "decline",
         "Italian shop, not Swiss", currency="EUR", delivery_by="2026-08-13",
         memory="U2 present = Everyday jacket at Loom and Pine"),
    Case("P3", "present", "2026-08-11T12:00:00Z", LOOM, [(COAT, 1, 95.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "fail"}, "decline",
         "Arrives Sat 15 Aug, after Friday", delivery_by="2026-08-15",
         memory="U2 present = Everyday jacket at Loom and Pine"),
    Case("P4", "present", "2026-08-11T13:00:00Z", LOOM, [(COAT, 1, 95.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "unknown"}, "step_up",
         "No delivery date given, and a different item: ask", delivery_by=None,
         memory="U2 present = Everyday jacket at Loom and Pine", customer="decline"),
    Case("P5", "present", "2026-08-11T14:00:00Z", PIXELHARBOR, [(MONITOR, 1, 289.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "pass"}, "step_up",
         "Arrives Fri 14 Aug exactly: 'by Friday' means on or before. Different shop and item from the one confirmed: ask",
         delivery_by="2026-08-14", memory="U2 present = Everyday jacket at Loom and Pine", customer="decline"),
    Case("P6", "present", "2026-08-12T09:00:00Z", LOOM, [(JACKET, 1, 145.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "pass"}, "step_up",
         "The remembered present again, 24 h after the approved one: the present check passes from memory, "
         "but it's a duplicate order (A3), so ask", delivery_by="2026-08-13",
         memory="U2 present = Everyday jacket at Loom and Pine", customer="decline"),
    Case("P7", "present", "2026-08-12T10:00:00Z", LOOM, [(COAT, 1, 900.0)],
         {"U2": "unknown", "C12-country": "pass", "C12-delivery": "pass"}, "step_up",
         "CHF 900: no amount was stated, so there is no amount rule (T2); the open question stands. "
         "Different item from the one confirmed: ask", delivery_by="2026-08-13",
         memory="U2 present = Everyday jacket at Loom and Pine", customer="decline"),

    # ---- Gym: "Renew my gym membership, same price as last time, ask me if anything changed"
    Case("G1", "gym", "2026-08-11T08:00:00Z", ALPINE_GYM, [(GYM, 1, 59.00)],
         {"C3": "pass", "C1-same": "pass", "C9-same": "pass", "C5": "pass"}, "approve",
         "Same gym, same CHF 59.00. Recurring billing was asked for, so it is not a hidden recurring cost (A6)",
         known_shop=True),
    Case("G2", "gym", "2026-08-12T08:00:00Z", ALPINE_GYM, [(GYM, 1, 64.00)],
         {"C3": "pass", "C1-same": "unknown", "C9-same": "pass", "C5": "pass"}, "step_up",
         "Price went up to CHF 64.00: the customer asked to be asked, not declined", known_shop=True,
         customer="approve"),
    Case("G3", "gym", "2026-08-13T08:00:00Z", LAKESIDE_GYM, [(GYM, 1, 59.00)],
         {"C3": "pass", "C1-same": "pass", "C9-same": "unknown", "C5": "pass"}, "step_up",
         "A different gym is a change: ask", known_shop=False, customer="decline"),
    Case("G4", "gym", "2026-08-14T08:00:00Z", ALPINE_GYM, [(MUSIC, 1, 59.00)],
         {"C3": "fail", "C1-same": "pass", "C9-same": "pass", "C5": "fail"}, "decline",
         "A music plan, not a gym membership", known_shop=True),
    Case("G5", "gym", "2026-08-17T08:00:00Z", ALPINE_GYM, [(GYM, 1, 59.00), (TOWEL, 1, 12.00)],
         {"C3": "pass", "C1-same": "unknown", "C9-same": "pass", "C5": "pass"}, "step_up",
         "A towel service was added: total CHF 71.00 is a change, so ask", known_shop=True, customer="decline"),
    Case("G6", "gym", "2026-08-20T08:00:00Z", ALPINE_GYM, [(GYM, 1, 62.10)],
         {"C3": "pass", "C1-same": "pass", "C9-same": "pass", "C5": "pass"}, "approve",
         "EUR 62.10 = CHF 59.00 exactly after half-even rounding (58.995 -> 59.00)", currency="EUR", known_shop=True),
]


def _event(c: Case) -> dict:
    merchant_id, name, category, country = c.shop
    items = [{"line_no": n, "item_id": it[0], "item_name": it[1], "item_category": it[2], "quantity": qty,
              "unit_price": price, "currency": c.currency, "item_details": ""}
             for n, (it, qty, price) in enumerate(c.lines, start=1)]
    amount = round(sum(q * p for _, q, p in c.lines), 2)
    return {"authorization": {
        "authorization_id": f"UNSEEN_{c.id}",
        "merchant": {"merchant_id": merchant_id, "merchant_name": name, "merchant_category": category,
                     "merchant_country": country},
        "timestamp": c.when, "amount": amount, "currency": c.currency, "billing_amount_chf": None,
        "customer_device_id": "DVC-T", "recent_attempt_count_10m": 0, "delivery_by": c.delivery_by,
        "order_returnable": "true", "order_cancellable": "unknown",
        "related_authorization_id": None, "related_authorization_status": None, "items": items,
    }}


def _rules(case: Case) -> list:
    facts = build_facts(_event(case))
    if case.known_shop is not None:
        facts = facts.model_copy(update={"merchant_known": case.known_shop})
    return [r for r in evaluate_rules(facts, POLICIES[case.instruction]) if r.rule_id not in STEP1_RULE_IDS]


def _needs_on_fail(case: Case) -> bool:
    return any(rid in ("C1-same", "C9-same") and out == "unknown" for rid, out in case.rules.items())


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_rule_results(case: Case):
    if _needs_on_fail(case) and not HAS_ON_FAIL:
        pytest.skip("needs Rule.on_fail (P2 contract request): 'ask me if anything changed'")
    got = {r.rule_id: r.outcome for r in _rules(case)}
    assert got == case.rules, f"{case.id}: {case.why}"


def test_answer_key_is_complete():
    assert {c.instruction for c in CASES} == set(POLICIES)
    for c in CASES:
        assert c.final in ("approve", "decline", "step_up")
        assert c.why
        if c.final == "step_up":
            assert c.customer in ("approve", "decline"), f"{c.id}: every ask needs a scripted answer"
        # A rule-level fail must lead to a decline (D1); an approve needs no fail and no unknown
        # that memory can't cover.
        if "fail" in c.rules.values():
            assert c.final == "decline", c.id


def test_every_ask_says_why():
    for c in CASES:
        for r in _rules(c):
            if r.outcome == "unknown":
                assert r.detail, f"{c.id} {r.rule_id} unknown without a reason"


# --- (b) final outcomes: the whole engine core, in order, with the customer's answers -----

def _final_expected(case: Case) -> str | None:
    """The expected outcome, adjusted while the contract PR is not merged (None = skip)."""
    if _needs_on_fail(case) and not HAS_ON_FAIL:
        return None  # "ask me if anything changed" needs Rule.on_fail
    if case.memory and case.final == "approve" and not HAS_CONFIRMED_KEYS:
        return "step_up"  # without LedgerView.confirmed_keys nothing is remembered: ask again
    return case.final


@pytest.mark.parametrize("instruction", sorted(POLICIES))
def test_final_outcomes(instruction, tmp_path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import sessionmaker

    from oneguard.engine.decide import decide
    from oneguard.engine.ledger import StoreLedger
    from oneguard.engine.ledger_base import LedgerEntry
    from oneguard.engine.policy import add_ledger_results
    from oneguard.engine.types import EvidenceRow, Signal
    from oneguard.store.db import init_db, make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'unseen.sqlite'}")
    init_db(engine)
    policy = POLICIES[instruction]
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        ledger = StoreLedger(session)
        for case in [c for c in CASES if c.instruction == instruction]:
            facts = build_facts(_event(case))
            if case.known_shop is not None:
                facts = facts.model_copy(update={"merchant_known": case.known_shop})
            view = ledger.view(run_id="unseen", customer_id="CU", card_id="CA", at=facts.timestamp,
                               period_days=None)
            rules = add_ledger_results(evaluate_rules(facts, policy), facts, policy, view)
            duplicate = "duplicate order (A3)" in case.why  # P5 detects it; decide combines
            prot = [Signal(id="A3", triggered=True, strength="protection", outcome_if_triggered="ask",
                           detail="same shop and item within 24 h", source="ledger")] if duplicate else []
            d = decide(rules, prot, [], [], policy, view)
            expected = _final_expected(case)
            if expected is not None:
                assert d.outcome == expected, f"{case.id}: {case.why} -> {d}"
            pending = d.outcome == "step_up"
            ledger.record(LedgerEntry(
                live_authorization_id=f"UNSEEN_{case.id}", run_id="unseen", mandate_id=policy.mandate_id,
                card_id="CA", customer_id="CU", ts_sim=facts.timestamp, outcome=d.outcome, final=not pending,
                uncertain_outcome="pending" if pending else None, merchant_id=facts.merchant_id,
                item_ids=[ln.item_id for ln in facts.items], billing_amount_chf=facts.billing_amount_chf,
                session_trust=d.session_trust, step=d.step, deciding_ids=d.deciding_ids,
                reason_codes=d.reason_codes, evidence=[EvidenceRow(rule="x", outcome="info", detail="x")],
                message="m", engine_version="test", latency_ms=0.0, signals_enabled=False, decided_at=now,
                deadline_at=now + timedelta(minutes=2) if pending else None,
            ))
            if pending and case.customer:
                ledger.resolve(f"UNSEEN_{case.id}", case.customer, "customer", now)
    engine.dispose()
