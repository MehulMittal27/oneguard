"""C1 "same price as last time at this shop" for a customer with several subscriptions
(lane request from #48).

Before this, ``value_from last_price`` resolved one price at compile time, from the latest
matching history row. A customer with several subscriptions has several last prices, so
SCEN0136's "If a price changes, ask me" compiled to a restriction no data can check. Now an
order-total rule can hold the reference ``LAST_PRICE_AT_SHOP``. The ledger view carries the
customer's last approved price per shop (history, then this run's final approvals), and each
purchase is compared with the last price at its own shop. With no earlier payment there the
check is unknown, never a pass (rule 4).

1. The ledger view, on both ledgers: history, then run approvals; declines and pending
   step-ups never set a price; a price the customer approved on a step-up does.
2. The rule: same, changed (asks under ``on_fail: ask``), no earlier price, "<=".
3. The compiler: SCEN0136 on both paths, "same price as last time" over several shops,
   the reference is no per-order cap, and the dry run.
4. SCEN0136 end to end through the pipeline, compiled on each path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

from oneguard.compiler import compile_instruction
from oneguard.compiler.dryrun import dry_run
from oneguard.compiler.lint import lint_accepted
from oneguard.compiler.parser import parse
from oneguard.engine.facts import build_facts
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger, LedgerEntry
from oneguard.engine.policy import LAST_PRICE_AT_SHOP, evaluate_last_price_rule
from oneguard.engine.types import HistoryRow, Policy, Rule
from oneguard.llm.provider import NullProvider
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store.db import init_db, make_engine
from oneguard.store.history import StoreHistoryIndex
from tests.test_c9_no_history import CARD, NEW, T0, event
from tests.test_compiler_judging import RECORDED_PATH, SERVED, Scripted

BILL = "authorization.billing_amount_chf"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
STREAM, CLOUD, GYM = ("ME_STREAM", "Streamly"), ("ME_CLOUD", "CloudBox"), ("ME_GYM", "Alpine Fitness")


def _row(n: int, shop: tuple[str, str], amount: float, days_before: int, *, status: str = "approved",
         description: str = "Monthly subscription") -> HistoryRow:
    return HistoryRow(
        authorization_id=f"H{n}", customer_id=NEW, card_id=CARD, initiator_type="merchant",
        timestamp=T0 - timedelta(days=days_before), transaction_type="purchase", status=status, amount=amount,
        currency="CHF", billing_amount_chf=amount, merchant_id=shop[0], merchant_name=shop[1],
        merchant_category="subscriptions", merchant_country="CH", channel="recurring", recurring=True,
        customer_device_id="DVC-NEW", description=description,
    )  # fmt: skip


def _history() -> StoreHistoryIndex:
    """Two subscriptions: Streamly went from 11.90 to 12.90, CloudBox is 4.99; a declined
    CloudBox attempt at 9.99 sets no price."""
    return StoreHistoryIndex(rows=[
        _row(1, STREAM, 11.90, 70), _row(2, STREAM, 12.90, 40), _row(3, CLOUD, 4.99, 35),
        _row(4, CLOUD, 9.99, 20, status="declined"),
    ])  # fmt: skip


# --- 1. The ledger view ----------------------------------------------------------------------
@contextmanager
def _ledger(kind: str, folder: Path) -> Iterator[Ledger]:
    if kind == "memory":
        yield InMemoryLedger(history=_history())
        return
    engine = make_engine(f"sqlite:///{folder / 'last-price.sqlite'}")
    init_db(engine)
    try:
        with Session(engine, expire_on_commit=False) as s:
            yield StoreLedger(s, history=_history())
    finally:
        engine.dispose()


def _entry(live: str, shop: tuple[str, str], amount: float, minutes: int, outcome: str = "approve") -> LedgerEntry:
    return LedgerEntry(
        live_authorization_id=live, run_id="run-lp", mandate_id="TM", card_id=CARD, customer_id=NEW,
        ts_sim=T0 + timedelta(minutes=minutes), outcome=outcome, final=outcome != "step_up",
        uncertain_outcome="pending" if outcome == "step_up" else None, merchant_id=shop[0], item_ids=["IT"],
        billing_amount_chf=amount, step=7 if outcome == "approve" else 2, deciding_ids=[], reason_codes=[],
        evidence=[], message="m", engine_version="t", latency_ms=1.0, signals_enabled=False, decided_at=NOW,
    )  # fmt: skip


@pytest.mark.parametrize("kind", ["memory", "store"])
def test_the_view_holds_the_last_approved_price_per_shop(kind, tmp_path):
    with _ledger(kind, tmp_path) as ledger:
        def prices(minutes: int) -> dict[str, float]:
            view = ledger.view(run_id="run-lp", customer_id=NEW, card_id=CARD,
                               at=T0 + timedelta(minutes=minutes), period_days=30)
            return view.last_price_chf_by_merchant

        assert prices(0) == {STREAM[0]: 12.90, CLOUD[0]: 4.99}  # history; the declined 9.99 is no price
        ledger.record(_entry("L1", STREAM, 14.90, 10))
        ledger.record(_entry("L2", CLOUD, 7.99, 20, outcome="decline"))
        ledger.record(_entry("L3", CLOUD, 6.99, 30, outcome="step_up"))
        assert prices(40) == {STREAM[0]: 14.90, CLOUD[0]: 4.99}  # declines and pending step-ups set nothing
        assert prices(5) == {STREAM[0]: 12.90, CLOUD[0]: 4.99}  # only approvals before the purchase
        ledger.resolve("L3", "approve", "customer", NOW)
        assert prices(40) == {STREAM[0]: 14.90, CLOUD[0]: 6.99}  # the customer's yes is the new price
        other = ledger.view(run_id="run-lp", customer_id="CU_SOMEONE_ELSE", card_id="CA_X", at=T0, period_days=30)
        assert other.last_price_chf_by_merchant == {}


# --- 2. The rule -----------------------------------------------------------------------------
def _rule(operator: str = "=", on_fail: str = "ask") -> Rule:
    return Rule(id="C1-same", field=BILL, operator=operator, value=LAST_PRICE_AT_SHOP, currency="CHF",
                scope="purchase", text="Total the same as your last payment at the same shop", source="inferred",
                kind="amount", on_fail=on_fail)


def _facts(shop: tuple[str, str], amount: float):
    ev = event(shop[0], amount=amount)
    ev["authorization"]["merchant"]["merchant_name"] = shop[1]
    return build_facts(ev)


def test_same_price_at_this_shop_passes():
    res = evaluate_last_price_rule(_rule(), _facts(STREAM, 12.90), 12.90)
    assert (res.outcome, res.detail) == ("pass", "CHF 12.90 at Streamly, the same as the CHF 12.90 you paid last time")


def test_a_changed_price_asks_when_the_customer_said_so_and_declines_otherwise():
    res = evaluate_last_price_rule(_rule(), _facts(STREAM, 14.90), 12.90)
    assert (res.outcome, res.detail, res.counterfactual) == (
        "unknown", "CHF 14.90 at Streamly; last time it was CHF 12.90; you asked to be asked if it changed",
        "Would approve at CHF 12.90, the price last time")
    assert evaluate_last_price_rule(_rule(on_fail="decline"), _facts(STREAM, 14.90), 12.90).outcome == "fail"


def test_no_earlier_price_at_this_shop_is_unknown_never_a_pass():
    res = evaluate_last_price_rule(_rule(), _facts(GYM, 59.0), None)
    assert (res.outcome, res.detail) == ("unknown", "You haven't paid Alpine Fitness before, so there is no last price to compare")


def test_at_or_below_last_time():
    assert evaluate_last_price_rule(_rule("<="), _facts(STREAM, 11.90), 12.90).outcome == "pass"
    res = evaluate_last_price_rule(_rule("<=", "decline"), _facts(STREAM, 14.90), 12.90)
    assert (res.outcome, res.detail, res.counterfactual) == (
        "fail", "CHF 14.90 at Streamly is over the CHF 12.90 you paid last time", "Would approve at CHF 12.90 or less")


# --- 3. The compiler -------------------------------------------------------------------------
def _recorded_response() -> dict:
    recorded = yaml.safe_load(RECORDED_PATH.read_text(encoding="utf-8"))
    return next(e["response"] for e in recorded if e["scenario"] == "SCEN0136")


def _provider(path: str):
    return NullProvider() if path == "fallback" else Scripted(_recorded_response())


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0136_compiles_the_price_clause_to_the_last_price_at_each_shop(path):
    """"If a price changes, ask me": C1-same on the reference, asking; no restriction no data
    can check stands for it; the question for a per-payment limit stays (the reference is no cap)."""
    draft = compile_instruction(SERVED["SCEN0136"], _history(), CARD, _provider(path), today=date(2026, 8, 1))
    assert draft.compiler == path
    rule = next(r for r in draft.rules if r.id == "C1-same")
    assert (rule.field, rule.operator, rule.value, rule.currency, rule.scope, rule.on_fail) == (
        BILL, "=", LAST_PRICE_AT_SHOP, "CHF", "purchase", "ask")
    assert rule.text == "Total the same as your last payment at the same shop; ask me if it changed"
    assert not [r for r in draft.rules if r.field == "unverifiable" and "price" in str(r.value)]
    assert draft.open_questions == ["No amount stated: what is the most one purchase may cost?"]
    assert not lint_accepted(draft.rules, [r.id for r in draft.rules]).ok  # still needs a per-order cap


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_same_price_as_last_time_over_several_shops_is_each_shops_price(path):
    """One shop in history: its price, as before (the gym). Several: the reference."""
    instruction = "Keep my subscriptions going at the same price as last time, ask me if anything changed"
    rule = {"field": BILL, "operator": "=", "value_number": None, "value_text": None, "value_list": None,
            "value_from": "last_price", "currency": "CHF", "scope": "purchase", "period_days": None,
            "words": "the same price as last time", "source": "inferred", "on_fail": "ask"}
    types = rule | {"field": "items[].item_category", "operator": "in", "value_list": ["subscriptions"],
                    "value_from": "literal", "currency": None, "scope": None, "words": "subscriptions",
                    "source": "exact", "on_fail": "decline"}
    reading = {"uncertainty_policy": "ask", "requested_item": None, "nothing_extra": False, "rules": [rule, types],
               "open_questions": ["No amount stated: what is the most one purchase may cost?"]}  # fmt: skip
    provider = NullProvider() if path == "fallback" else Scripted(reading)
    draft = compile_instruction(instruction, _history(), CARD, provider, today=date(2026, 8, 1))
    assert draft.compiler == path
    assert [(r.field, r.value, r.on_fail) for r in draft.rules if r.field == BILL] == [(BILL, LAST_PRICE_AT_SHOP, "ask")]

    one_shop = StoreHistoryIndex(rows=[_row(1, GYM, 59.0, 30, description="Gym membership")])
    draft = compile_instruction("Renew my gym membership, same price as last time", one_shop, CARD, NullProvider(),
                                today=date(2026, 8, 1))
    assert [(r.field, r.value) for r in draft.rules if r.field == BILL] == [(BILL, 59)]


def test_the_dry_run_compares_each_purchase_with_its_own_shops_last_price():
    draft = parse(SERVED["SCEN0136"], _history(), CARD, date(2026, 8, 1))
    only_price = draft.model_copy(update={"rules": [r for r in draft.rules if r.id == "C1-same"],
                                          "requires_known_shop": False})
    result = dry_run(only_price, _history(), CARD, NEW)
    reasons = sorted((e.merchant_name, e.outcome, e.reason) for e in result.examples or [])
    assert reasons == [
        ("CloudBox", "ask", "first payment at CloudBox, no earlier price"),
        ("Streamly", "ask", "CHF 12.90 vs CHF 11.90 last time"),
        ("Streamly", "ask", "first payment at Streamly, no earlier price"),
    ]


# --- 4. SCEN0136 end to end --------------------------------------------------------------------
@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0136_asks_on_a_changed_price_at_its_own_shop_and_learns_the_approved_one(path):
    """Compiled on each path. Streamly at its last price and CloudBox at its (different) last
    price both pass C1-same; Streamly at a new price asks on C1-same; once the customer
    approves it, the new price is Streamly's last price."""
    history = _history()
    instruction = SERVED["SCEN0136"]
    draft = compile_instruction(instruction, history, CARD, _provider(path), today=date(2026, 8, 1))
    policy = Policy(mandate_id="TM_NEW", status="active", instruction=instruction,
                    uncertainty_policy=draft.uncertainty_policy, rules=draft.rules,
                    allowed_item_categories=draft.allowed_item_categories,
                    requires_known_shop=draft.requires_known_shop)
    ledger = InMemoryLedger(history=history)
    ctx = PipelineContext(policy=policy, ledger=ledger, history=history, run_id=f"run-136-{path}", now=lambda: NOW)
    label = next(r.text for r in draft.rules if r.id == "C1-same")

    def pay(shop: tuple[str, str], amount: float, minutes: int):
        ev = event(shop[0], item=f"IT_{shop[0]}", minutes=minutes, amount=amount)
        ev["authorization"]["merchant"].update(merchant_name=shop[1], merchant_category="subscriptions")
        ev["authorization"]["items"][0].update(item_category="subscriptions", item_name=f"{shop[1]} plan")
        decision, explanation, _ = decide_event(ev, ctx)
        row = next(r for r in explanation.evidence if r.rule == label)
        return ev, decision, row

    _, decision, row = pay(STREAM, 12.90, 0)
    assert (row.outcome, row.detail) == ("pass", "CHF 12.90 at Streamly, the same as the CHF 12.90 you paid last time")
    assert "C1-same" not in decision.deciding_ids
    _, decision, row = pay(CLOUD, 4.99, 10)
    assert row.outcome == "pass" and "C1-same" not in decision.deciding_ids

    changed, decision, row = pay(STREAM, 14.90, 20)
    assert decision.outcome == "step_up" and "C1-same" in decision.deciding_ids
    assert (row.outcome, row.detail) == (
        "uncertain", "CHF 14.90 at Streamly; last time it was CHF 12.90; you asked to be asked if it changed")
    ledger.resolve(changed["authorization"]["authorization_id"], "approve", "customer", NOW)

    _, decision, row = pay(STREAM, 14.90, 30)
    assert (row.outcome, row.detail) == ("pass", "CHF 14.90 at Streamly, the same as the CHF 14.90 you paid last time")
    assert "C1-same" not in decision.deciding_ids
