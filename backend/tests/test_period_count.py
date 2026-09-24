"""A purchase count per period: ``cart.purchases_in_period`` (api-contract §3.3, rules.md C2).

Synthetic purchases on one card through the real pipeline (``decide_event`` with every
engine function real), over both ledgers: final approvals and pending step-ups count,
declines never do, a redelivered live id counts once, and the window rolls on simulated
time. No scenario ids.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from oneguard.engine import stubs
from oneguard.engine.interfaces import load_implementations
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger, LedgerEntry
from oneguard.engine.policy import COUNT_FIELD, add_ledger_results, evaluate_count_rule
from oneguard.engine.types import HistoryRow, LedgerView, Policy, Rule
from oneguard.pipeline import PipelineContext, decide_event, period_days_of
from oneguard.store.db import init_db, make_engine
from oneguard.store.history import StoreHistoryIndex

REPO = Path(__file__).resolve().parents[2]
EXAMPLE_EVENT = REPO / "data" / "scenario_fixtures" / "example_authorization_request.json"
CARD, OTHER_CARD, RUN = "CA_EXAMPLE_0001", "CA_EXAMPLE_0002", "run_count"
MON = datetime(2026, 9, 21, tzinfo=UTC)  # Monday; Zurich is UTC+2 in September
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)  # real clock (deadlines only)
REAL, _ = stubs.select_implementations("none", load_implementations())


def _rule(allowed: int, days: int, op: str = "<=") -> Rule:
    return Rule(id="C12-count", field=COUNT_FIELD, operator=op, value=allowed, scope="period",
                period_days=days, text=f"At most {allowed} orders in {days} days", source="exact",
                kind="period")


def _policy(allowed: int, days: int) -> Policy:
    cap = Rule(id="C1", field="authorization.billing_amount_chf", operator="<=", value=40,
               currency="CHF", scope="purchase", text="Total at or below CHF 40 per order", source="exact")
    return Policy(mandate_id="TM_COUNT", status="active", instruction="One delivery a day, CHF 40 max.",
                  rules=[cap, _rule(allowed, days)], uncertainty_policy="ask")


_seq = iter(range(1, 10**6))


def _event(at: datetime, amount: float = 30.0, card: str = CARD, live_id: str | None = None) -> dict:
    """A clean purchase at a fresh shop (so no repeat-order protection fires)."""
    n = next(_seq)
    event = json.loads(EXAMPLE_EVENT.read_text(encoding="utf-8"))
    auth = event["authorization"]
    live_id = live_id or f"LIVE_COUNT_{n:04d}"
    auth.update(authorization_id=live_id, source_authorization_id=live_id, card_id=card,
                timestamp=at.isoformat().replace("+00:00", "Z"), amount=amount, billing_amount_chf=amount)
    auth["merchant"].update(merchant_id=f"ME_COUNT_{n}", merchant_name=f"Dinner Service {n}",
                            merchant_category="food_delivery")
    auth["items"][0].update(item_id=f"IT_COUNT_{n}", item_name="Dinner", item_category="food_delivery",
                            unit_price=amount)
    event["mandate"]["card_id"] = card
    return event


def _history() -> StoreHistoryIndex:
    """A customer with a usual device, country and amount, so no warning sign fires."""
    row = HistoryRow(
        authorization_id="H_COUNT_1", customer_id="CU_EXAMPLE_0001", card_id=CARD, initiator_type="human",
        timestamp=MON - timedelta(days=30), transaction_type="purchase", status="approved",
        amount=80.0, currency="CHF", billing_amount_chf=80.0, merchant_id="ME_COUNT_HISTORY",
        merchant_name="Corner Shop", merchant_category="groceries", merchant_country="CH",
        channel="ecommerce", recurring=False, customer_device_id="DVC-EXAMPLE", description="",
    )  # fmt: skip
    return StoreHistoryIndex(rows=[row])


HISTORY = _history()


@pytest.fixture(params=["memory", "store"])
def make_ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Callable[[], Ledger]]:
    if request.param == "memory":
        yield lambda: InMemoryLedger(history=HISTORY)
        return
    engine = make_engine(f"sqlite:///{tmp_path / 'count.sqlite'}")
    init_db(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    sessions = []

    def build() -> Ledger:
        sessions.append(maker())
        return StoreLedger(sessions[-1], HISTORY)

    yield build
    for s in sessions:
        s.close()
    engine.dispose()


def _ctx(ledger: Ledger, policy: Policy) -> PipelineContext:
    return PipelineContext(policy=policy, ledger=ledger, history=HISTORY, run_id=RUN,
                           implementations=REAL, stubbed=frozenset(), now=lambda: NOW)


def _decide(ctx: PipelineContext, event: dict) -> Any:
    return decide_event(copy.deepcopy(event), ctx)


def _view(ledger: Ledger, at: datetime, days: int, card: str = CARD) -> LedgerView:
    return ledger.view(run_id=RUN, customer_id="CU_EXAMPLE_0001", card_id=card, at=at, period_days=days)


def _zurich(day: datetime, hh: int, mm: int) -> datetime:
    return day + timedelta(hours=hh - 2, minutes=mm)


# --- One a day -------------------------------------------------------------------------


def test_one_a_day_second_order_the_same_day_is_declined(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))

    first = _event(_zurich(MON, 12, 10))
    engine, _, _ = _decide(ctx, first)
    assert engine.outcome == "approve", engine

    engine, explanation, decision = _decide(ctx, _event(_zurich(MON, 18, 30), amount=32.0))
    assert (engine.outcome, engine.reason_codes, engine.deciding_ids) == (
        "decline", ["period_count_exceeded"], ["C12-count"])
    row = next(r for r in explanation.evidence if r.rule == "At most 1 orders in 1 days")
    assert (row.outcome, row.detail) == ("fail", "You allowed one order per day; one was already approved today at 12:10")
    assert decision.message == ("Declined CHF 32.00: You allowed one order per day; one was already approved "
                                "today at 12:10; would approve from tomorrow at 12:10.")
    assert decision.counterfactual == "Would approve from tomorrow at 12:10."


def test_a_decline_never_counts_and_the_next_day_passes(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))
    assert _decide(ctx, _event(_zurich(MON, 12, 10)))[0].outcome == "approve"
    over_cap = _decide(ctx, _event(_zurich(MON, 13, 0), amount=55.0))[0]  # declined by C1 (and C12)
    assert over_cap.outcome == "decline"

    # Tuesday 12:20: Monday's 12:10 approval is out of the 24 h window; Monday's 13:00 decline never counted.
    tuesday = _zurich(MON + timedelta(days=1), 12, 20)
    assert _view(ledger, tuesday, 1).period_count == 0
    engine, explanation, _ = _decide(ctx, _event(tuesday))
    assert engine.outcome == "approve", engine
    row = next(r for r in explanation.evidence if r.rule == "At most 1 orders in 1 days")
    assert (row.outcome, row.detail) == ("pass", "You allowed one order per day; this is order one")


def test_a_decline_alone_leaves_room(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))
    assert _decide(ctx, _event(_zurich(MON, 11, 0), amount=55.0))[0].outcome == "decline"
    assert _view(ledger, _zurich(MON, 12, 0), 1).period_count == 0
    assert _decide(ctx, _event(_zurich(MON, 12, 0)))[0].outcome == "approve"


def test_redelivery_counts_nothing(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(2, 1))
    first = _event(_zurich(MON, 12, 10))
    assert _decide(ctx, first)[0].outcome == "approve"
    assert _decide(ctx, first)[0].outcome == "approve"  # same live id: the stored result
    assert _view(ledger, _zurich(MON, 13, 0), 1).period_count == 1
    assert _decide(ctx, _event(_zurich(MON, 13, 0)))[0].outcome == "approve"  # two of two


def test_a_pending_step_up_is_reserved(make_ledger) -> None:
    """A step-up still waiting counts; if it alone breaks the count, the next one asks (M5)."""
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))
    waiting_at = _zurich(MON, 12, 10)
    ledger.record(_pending("LIVE_COUNT_WAITING", waiting_at))

    view = _view(ledger, _zurich(MON, 18, 0), 1)
    assert (view.period_count, view.period_reserved_count, view.period_last_approved_at) == (1, 1, None)
    engine, _, decision = _decide(ctx, _event(_zurich(MON, 18, 0), live_id="LIVE_COUNT_ASKED_AGAIN"))
    assert (engine.outcome, engine.reason_codes) == ("step_up", ["period_reserved_pending"])
    assert decision.message == ("Waiting for you CHF 30.00: You allowed one order per day; "
                                "one is still waiting for your answer.")
    assert decision.counterfactual == "Would approve if you decline the order still waiting."

    ledger.resolve("LIVE_COUNT_WAITING", "decline", "customer", NOW)  # released: counts nothing
    assert _view(ledger, _zurich(MON, 19, 0), 1).period_count == 1  # the 18:00 step-up now waits
    ledger.resolve("LIVE_COUNT_ASKED_AGAIN", "decline", "customer", NOW)
    assert _decide(ctx, _event(_zurich(MON, 19, 0)))[0].outcome == "approve"


def test_an_approved_step_up_is_spend(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))
    ledger.record(_pending("LIVE_COUNT_ASKED", _zurich(MON, 9, 45)))
    ledger.resolve("LIVE_COUNT_ASKED", "approve", "customer", NOW)
    engine, explanation, _ = _decide(ctx, _event(_zurich(MON, 18, 0)))
    assert (engine.outcome, engine.reason_codes) == ("decline", ["period_count_exceeded"])
    assert "one was already approved today at 09:45" in explanation.message


def test_other_cards_do_not_count(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1))
    assert _decide(ctx, _event(_zurich(MON, 12, 10), card=OTHER_CARD))[0].outcome == "approve"
    assert _view(ledger, _zurich(MON, 13, 0), 1).period_count == 0
    assert _decide(ctx, _event(_zurich(MON, 13, 0)))[0].outcome == "approve"


# --- Per week ----------------------------------------------------------------------------


def test_two_a_week_the_third_order_in_the_week_fails(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(2, 7))
    assert _decide(ctx, _event(_zurich(MON, 19, 0)))[0].outcome == "approve"
    assert _decide(ctx, _event(_zurich(MON + timedelta(days=2), 19, 5)))[0].outcome == "approve"

    friday = _zurich(MON + timedelta(days=4), 19, 0)
    engine, explanation, _ = _decide(ctx, _event(friday))
    assert (engine.outcome, engine.reason_codes) == ("decline", ["period_count_exceeded"])
    assert explanation.message == (
        "Declined CHF 30.00: You allowed two orders per week; two were already approved, the last "
        "on Wed 23 Sep at 19:05; would approve once fewer than three orders fall in the last 7 days.")

    # Next Monday 19:30: the first Monday order is more than 7 days old.
    assert _decide(ctx, _event(_zurich(MON + timedelta(days=7), 19, 30)))[0].outcome == "approve"


# --- The rule on its own -----------------------------------------------------------------


def test_not_counted_is_never_a_pass(make_ledger) -> None:
    ledger = make_ledger()
    facts = REAL["build_facts"](_event(_zurich(MON, 12, 0)), HISTORY)
    view = _view(ledger, facts.timestamp, 1).model_copy(update={"period_count": None})
    assert evaluate_count_rule(_rule(1, 1), facts, view).outcome == "unknown"

    # A count rule over a window the view does not cover (the view is the shortest period).
    policy = _policy(1, 1).model_copy(update={"rules": [_rule(1, 1), _rule(3, 7).model_copy(update={"id": "W"})]})
    assert period_days_of(policy) == 1
    results = add_ledger_results([], facts, policy, _view(ledger, facts.timestamp, 1))
    assert [(r.rule_id, r.outcome) for r in results] == [("C12-count", "pass"), ("W", "unknown")]

    # Not a cap: only "<" and "<=" are read as a count limit.
    assert evaluate_count_rule(_rule(1, 1, op=">="), facts, _view(ledger, facts.timestamp, 1)).outcome == "unknown"


def test_strictly_fewer_than(make_ledger) -> None:
    ledger = make_ledger()
    ctx = _ctx(ledger, _policy(1, 1).model_copy(update={"rules": [_rule(2, 1, op="<")]}))
    assert _decide(ctx, _event(_zurich(MON, 12, 10)))[0].outcome == "approve"
    engine, explanation, _ = _decide(ctx, _event(_zurich(MON, 12, 40)))
    assert engine.reason_codes == ["period_count_exceeded"]
    assert "You allowed one order per day" in explanation.message


def test_spend_reconciliation_ignores_counts() -> None:
    policy = _policy(1, 1)
    assert period_days_of(policy) == 1
    assert period_days_of(policy, spend_only=True) is None


# --- helpers -------------------------------------------------------------------------------


def _pending(live_id: str, at: datetime) -> LedgerEntry:
    return LedgerEntry(
        live_authorization_id=live_id, run_id=RUN, mandate_id="TM_COUNT", card_id=CARD,
        customer_id="CU_EXAMPLE_0001", ts_sim=at, outcome="step_up", final=False,
        uncertain_outcome="pending", merchant_id="ME_COUNT_WAITING", item_ids=["IT_COUNT_WAITING"],
        billing_amount_chf=25.0, step=4, deciding_ids=["U1"], reason_codes=["unevaluable"], evidence=[],
        message="m", engine_version="t", latency_ms=1.0, signals_enabled=False, decided_at=NOW,
        deadline_at=NOW + timedelta(minutes=2),
    )  # fmt: skip

