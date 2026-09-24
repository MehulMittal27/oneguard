"""C9 known shop for a customer with no purchase history yet (rules.md C9).

A customer with no approved purchase in history (any card) and none in this run cannot
satisfy "a shop I use regularly" from history, so C9 is unknown, not a fail: the
uncertainty setting decides. Once the customer approves one purchase at a shop, the next
purchase there passes C9 (this run's approvals, or the remembered answer per shop in a
later live session). Unverifiable restrictions stay remembered per shop and item.

A synthetic customer through the real pipeline, on both ledgers. Another customer's
history at the same shop proves the check is per customer.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger
from oneguard.engine.policy import (
    CONFIRMED,
    NO_HISTORY,
    add_ledger_results,
    evaluate_rules,
)
from oneguard.engine.types import HistoryRow, Policy, Rule
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store.db import init_db, make_engine
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import Run

T0 = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)  # simulated time of the first purchase
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)  # real clock
NEW, OTHER, CARD, MANDATE = "CU_NEW", "CU_OTHER", "CA_NEW", "TM_NEW"
SHOP, SHOP2 = "ME_SHOP", "ME_SHOP2"

C1 = Rule(id="C1", field="authorization.billing_amount_chf", operator="<=", value=50, currency="CHF",
          scope="purchase", text="Total at or below CHF 50 per order", source="exact", kind="amount")
C9 = Rule(id="C9", field="merchant.familiar_on_card", operator="=", value="true",
          text="Only shops you have bought from before", source="inferred", kind="merchant")
U1 = Rule(id="U1", field="unverifiable", operator="=", value="official seller",
          text="from the official seller", source="exact")


def policy(setting: str = "ask", *, typed: bool = True, rules: list[Rule] | None = None) -> Policy:
    """"A shop I use regularly": the typed rule (compiler, Viseca hard rule) or only the flag."""
    if rules is None:
        rules = [C1, C9] if typed else [C1]
    return Policy(mandate_id=MANDATE, status="active", instruction="Buy groceries from a shop I use regularly.",
                  uncertainty_policy=setting, rules=rules, requires_known_shop=C9 in rules or not typed)


def history() -> StoreHistoryIndex:
    """Someone else bought at SHOP; the new customer has no row at all."""
    return StoreHistoryIndex(rows=[HistoryRow(
        authorization_id="H1", customer_id=OTHER, card_id="CA_OTHER", initiator_type="human",
        timestamp=T0 - timedelta(days=5), transaction_type="purchase", status="approved", amount=20.0,
        currency="CHF", billing_amount_chf=20.0, merchant_id=SHOP, merchant_name="Corner Grocer",
        merchant_category="groceries", merchant_country="CH", channel="ecommerce", recurring=False,
        customer_device_id="DVC-OTHER", description="groceries",
    )])  # fmt: skip


_seq = iter(range(1, 10**9))


def event(merchant: str = SHOP, item: str = "IT_A", *, minutes: int = 0, amount: float = 30.0) -> dict:
    live = f"LIVE_NEW_{next(_seq):04d}"
    return {
        "type": "authorization.request", "request_id": f"req_{live}", "deadline_at": "2026-09-25T12:00:08Z",
        "authorization": {
            "authorization_id": live, "source_authorization_id": live, "mandate_id": MANDATE,
            "card_id": CARD, "initiator_type": "agent",
            "merchant": {"merchant_id": merchant, "merchant_name": f"Shop {merchant}",
                         "merchant_category": "groceries", "merchant_mcc": "5411", "merchant_country": "CH",
                         "recurring_capable": "false"},
            "timestamp": (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "amount": amount, "currency": "CHF", "billing_amount_chf": amount, "channel": "ecommerce",
            "customer_device_id": "DVC-NEW", "authority_status": "active", "card_status_at_attempt": "active",
            "recent_attempt_count_10m": 0, "delivery_by": None, "order_returnable": "true",
            "order_cancellable": "unknown", "related_authorization_id": None,
            "related_authorization_status": None, "purchase_description": "Grocery order",
            "items": [{"line_no": 1, "item_id": item, "item_name": "Fresh produce", "item_category": "groceries",
                       "quantity": 1, "unit_price": amount, "currency": "CHF", "item_details": ""}],
        },
        "mandate": {"mandate_id": MANDATE, "status": "active", "customer_id": NEW, "card_id": CARD,
                    "instruction": "Buy groceries from a shop I use regularly.", "hard_rules": [],
                    "uncertainty_policy": "ask"},
        "context": {"approved_spend_in_period_chf": None, "recent_authorizations": []},
    }  # fmt: skip


@contextmanager
def make_ledger(kind: str, folder: Path) -> Iterator[Ledger]:
    if kind == "memory":
        yield InMemoryLedger(history=history())
        return
    engine = make_engine(f"sqlite:///{folder / f'c9-{next(_seq)}.sqlite'}")
    init_db(engine)
    try:
        with Session(engine, expire_on_commit=False) as s:
            yield StoreLedger(s, history=history())
    finally:
        engine.dispose()


def ctx(ledger: Ledger, pol: Policy, run_id: str = "run-new") -> PipelineContext:
    return PipelineContext(policy=pol, ledger=ledger, history=history(), run_id=run_id, now=lambda: NOW)


def c9_row(explanation, pol: Policy):
    label = C9.text if C9 in pol.rules else "A shop you have bought from"
    return next(row for row in explanation.evidence if row.rule == label)


LEDGERS = ["store", "memory"]


# --- first purchase: unknown, the uncertainty setting decides --------------------------


@pytest.mark.parametrize("kind", LEDGERS)
@pytest.mark.parametrize("typed", [True, False], ids=["typed-rule", "flag-only"])
@pytest.mark.parametrize(
    ("setting", "outcome", "lead"),
    [("ask", "step_up", "Waiting for you"), ("decline", "decline", "Declined")],
)
def test_first_purchase_follows_the_uncertainty_setting(tmp_path, kind, typed, setting, outcome, lead):
    pol = policy(setting, typed=typed)
    with make_ledger(kind, tmp_path) as ledger:
        engine, explanation, _ = decide_event(event(), ctx(ledger, pol))
    assert (engine.outcome, engine.step) == (outcome, 4)
    assert engine.deciding_ids == ["C9"] and engine.reason_codes == ["unevaluable"]
    # No baseline yet: W1, W3, W4 are info only, so the message names C9 alone.
    assert explanation.message == f"{lead} CHF 30.00: {NO_HISTORY}."
    row = c9_row(explanation, pol)
    assert (row.outcome, row.detail, row.source) == ("uncertain", NO_HISTORY, "history")
    assert "no purchase history yet" in NO_HISTORY


@pytest.mark.parametrize("kind", LEDGERS)
def test_asking_rule_reads_no_history_too(tmp_path, kind):
    """A known-shop rule that asks when broken ("ask me if anything changed")."""
    asking = C9.model_copy(update={"on_fail": "ask"})
    pol = policy(rules=[C1, asking])
    with make_ledger(kind, tmp_path) as ledger:
        engine, explanation, _ = decide_event(event(), ctx(ledger, pol))
    assert engine.outcome == "step_up"
    assert explanation.message == f"Waiting for you CHF 30.00: {NO_HISTORY}."


# --- after one approval: the shop is known ----------------------------------------------


@pytest.mark.parametrize("kind", LEDGERS)
@pytest.mark.parametrize("typed", [True, False], ids=["typed-rule", "flag-only"])
def test_after_one_approval_the_same_shop_passes(tmp_path, kind, typed):
    pol = policy(typed=typed)
    with make_ledger(kind, tmp_path) as ledger:
        c = ctx(ledger, pol)
        first = event(item="IT_A")
        engine, _, _ = decide_event(first, c)
        assert engine.outcome == "step_up"
        ledger.resolve(first["authorization"]["authorization_id"], "approve", "customer", NOW)

        engine, again, _ = decide_event(event(item="IT_B", minutes=60), c)
        assert c9_row(again, pol).outcome == "pass" and "C9" not in engine.deciding_ids

        # Now the customer has history: an unknown shop is a plain fail again (unchanged).
        engine, other, _ = decide_event(event(merchant=SHOP2, minutes=120), c)
        assert (engine.outcome, engine.step, engine.reason_codes) == ("decline", 2, ["unfamiliar_merchant"])
        assert c9_row(other, pol).outcome == "fail"


@pytest.mark.parametrize("kind", LEDGERS)
def test_a_declined_first_purchase_teaches_nothing(tmp_path, kind):
    pol = policy()
    with make_ledger(kind, tmp_path) as ledger:
        c = ctx(ledger, pol)
        first = event()
        decide_event(first, c)
        ledger.resolve(first["authorization"]["authorization_id"], "decline", "customer", NOW)
        _, again, _ = decide_event(event(minutes=60), c)
    assert c9_row(again, pol).detail == NO_HISTORY


# --- remembered per shop, whatever the item ---------------------------------------------


def _approve_step_up(ledger: Ledger, pol: Policy, run_id: str, item: str) -> None:
    ev = event(item=item)
    engine, _, _ = decide_event(ev, ctx(ledger, pol, run_id))
    assert engine.outcome == "step_up", engine
    ledger.resolve(ev["authorization"]["authorization_id"], "approve", "customer", NOW)


def _memory_only(ledger: Ledger, pol: Policy, run_id: str, ev: dict):
    """Rule results for ``ev`` from the remembered answers alone: this run's approvals are
    dropped from the known shops, as in a later session with no purchase history."""
    from oneguard.engine.facts import build_facts

    facts = build_facts(ev, history())
    view = ledger.view(run_id=run_id, customer_id=NEW, card_id=CARD, at=facts.timestamp, period_days=None)
    view = view.model_copy(update={"known_merchant_ids": set(), "known_merchant_ids_on_card": set()})
    facts = facts.model_copy(update={"merchant_known": False, "merchant_known_on_card": False})
    return {r.rule_id: r for r in add_ledger_results(evaluate_rules(facts, pol), facts, pol, view)}


@pytest.mark.parametrize("kind", LEDGERS)
def test_c9_is_remembered_per_shop_not_per_item(tmp_path, kind):
    pol = policy()
    with make_ledger(kind, tmp_path) as ledger:
        _approve_step_up(ledger, pol, "run-new", "IT_A")
        other_item = _memory_only(ledger, pol, "run-new", event(item="IT_B", minutes=60))["C9"]
        other_shop = _memory_only(ledger, pol, "run-new", event(merchant=SHOP2, minutes=60))["C9"]
    assert (other_item.outcome, other_item.detail) == ("pass", f"{CONFIRMED} shop earlier")
    assert (other_shop.outcome, other_shop.detail) == ("unknown", NO_HISTORY)


@pytest.mark.parametrize("kind", LEDGERS)
def test_unverifiable_rule_stays_per_shop_and_item(tmp_path, kind):
    pol = policy(rules=[C1, U1])
    with make_ledger(kind, tmp_path) as ledger:
        _approve_step_up(ledger, pol, "run-new", "IT_A")
        same_item = _memory_only(ledger, pol, "run-new", event(item="IT_A", minutes=60))["U1"]
        other_item = _memory_only(ledger, pol, "run-new", event(item="IT_B", minutes=60))["U1"]
    assert same_item.outcome == "pass" and same_item.detail.startswith(CONFIRMED)
    assert other_item.outcome == "unknown"


def test_next_live_session_remembers_the_shop(tmp_path):
    """StoreLedger: the answer carries to a later live session under the same mandate,
    where the customer still has no history and no approval in that run."""
    pol = policy()
    with make_ledger("store", tmp_path) as ledger:
        for i, run_id in enumerate(("live-1", "live-2")):
            ledger.session.add(Run(run_id=run_id, kind="live", mandate_id=MANDATE, card_id=CARD,
                                   state="running", started_at=NOW + timedelta(days=i)))
        ledger.session.commit()
        _approve_step_up(ledger, pol, "live-1", "IT_A")

        engine, other_shop, _ = decide_event(event(merchant=SHOP2, minutes=30), ctx(ledger, pol, "live-2"))
        assert engine.outcome == "step_up" and c9_row(other_shop, pol).detail == NO_HISTORY

        engine, same_shop, _ = decide_event(event(item="IT_B", minutes=60), ctx(ledger, pol, "live-2"))
        row = c9_row(same_shop, pol)
        assert (row.outcome, row.detail) == ("pass", f"{CONFIRMED} shop earlier")
        assert engine.outcome == "approve" and "customer_confirmation" in engine.reason_codes
