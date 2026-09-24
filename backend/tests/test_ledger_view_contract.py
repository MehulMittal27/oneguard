"""Both ledgers fill the contract fields: PriorDecision.approved, known_merchant_names,
confirmed_keys (InMemoryLedger and StoreLedger give the same view)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from oneguard.engine.explain import expired_message
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger, LedgerEntry
from oneguard.engine.types import HistoryIndex, HistoryRow
from oneguard.store.db import init_db, make_engine
from oneguard.store.history import StoreHistoryIndex

T0 = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
NAMES = {"ME1": "PixelHarbor", "ME2": "Alpine Basket", "ME3": "Never Bought"}


def _history() -> StoreHistoryIndex:
    row = HistoryRow(
        authorization_id="H1", customer_id="CU1", card_id="CA1", initiator_type="human",
        timestamp=T0 - timedelta(days=3), transaction_type="purchase", status="approved",
        amount=50.0, currency="CHF", billing_amount_chf=50.0, merchant_id="ME1",
        merchant_name="PixelHarbor", merchant_category="electronics", merchant_country="CH",
        channel="online", recurring=False, customer_device_id="DVC-1", description="",
    )  # fmt: skip
    return StoreHistoryIndex(rows=[row], merchant_names=NAMES)


def _entry(
    live_id: str,
    outcome: str,
    merchant: str = "ME2",
    minutes: int = 30,
    deciding_ids: tuple[str, ...] = (),
) -> LedgerEntry:
    return LedgerEntry(
        live_authorization_id=live_id, run_id="run", mandate_id="M", card_id="CA1",
        customer_id="CU1", ts_sim=T0 - timedelta(minutes=minutes), outcome=outcome,
        final=outcome != "step_up", uncertain_outcome="pending" if outcome == "step_up" else None,
        merchant_id=merchant, item_ids=["IT1"], billing_amount_chf=40.0, step=4,
        deciding_ids=list(deciding_ids), reason_codes=[], evidence=[], message="m",
        engine_version="t", latency_ms=1.0, signals_enabled=False, decided_at=T0,
    )  # fmt: skip


MakeLedger = Callable[[HistoryIndex | None], Ledger]


@pytest.fixture(params=["memory", "store"])
def make_ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MakeLedger]:
    if request.param == "memory":
        yield lambda history: InMemoryLedger(history=history)
        return
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield lambda history: StoreLedger(session, history=history)
    engine.dispose()


def _view(ledger: Ledger):
    return ledger.view(
        run_id="run", customer_id="CU1", card_id="CA1", at=T0, period_days=None
    )


def test_history_index_names_only_catalogue_merchants():
    history = _history()
    assert history.merchant_names(["ME1", "ME_UNKNOWN"]) == {"ME1": "PixelHarbor"}
    assert history.merchant_names_normalised()["ME2"] == "alpinebasket"


@pytest.mark.parametrize(
    ("answer", "resolved_by", "approved"),
    [(None, None, False), ("approve", "customer", True), ("decline", "customer", False),
     ("decline", "timeout", False)],
)  # fmt: skip
def test_prior_approved_is_true_only_for_final_approvals(
    make_ledger: MakeLedger, answer, resolved_by, approved
):
    ledger = make_ledger(_history())
    ledger.record(_entry("LIVE-A", "approve", minutes=50))
    ledger.record(_entry("LIVE-D", "decline", minutes=40))
    ledger.record(_entry("LIVE-S", "step_up"))
    if answer:
        ledger.resolve("LIVE-S", answer, resolved_by, T0,
                       message=expired_message(120) if resolved_by == "timeout" else None)
    priors = {p.authorization_id: p for p in _view(ledger).priors}
    assert priors["LIVE-A"].approved and not priors["LIVE-D"].approved
    assert priors["LIVE-S"].approved is approved


def test_known_merchant_names_cover_history_and_this_runs_approvals(
    make_ledger: MakeLedger,
):
    ledger = make_ledger(_history())
    assert _view(ledger).known_merchant_names == {"ME1": "PixelHarbor"}
    ledger.record(_entry("LIVE-A", "approve", merchant="ME2"))
    ledger.record(_entry("LIVE-D", "decline", merchant="ME3", minutes=20))
    ledger.record(
        _entry("LIVE-X", "approve", merchant="ME_NOT_IN_CATALOGUE", minutes=10)
    )
    view = _view(ledger)
    assert "ME_NOT_IN_CATALOGUE" in view.known_merchant_ids
    assert view.known_merchant_names == {"ME1": "PixelHarbor", "ME2": "Alpine Basket"}


def test_without_a_history_index_there_are_no_names(make_ledger: MakeLedger):
    ledger = make_ledger(None)
    ledger.record(_entry("LIVE-A", "approve"))
    assert _view(ledger).known_merchant_names == {}


@pytest.mark.parametrize(
    ("answer", "resolved_by", "keys"),
    [(None, None, set()), ("approve", "customer", {"C5|ME2|IT1", "A6|ME2|IT1", "C5|ME2|*", "A6|ME2|*"}),
     ("decline", "customer", set()), ("decline", "timeout", set())],
)  # fmt: skip
def test_confirmed_keys_remember_only_step_ups_the_customer_approved(
    make_ledger: MakeLedger, answer, resolved_by, keys
):
    ledger = make_ledger(_history())
    ledger.record(_entry("LIVE-S", "step_up", deciding_ids=("C5", "A6")))
    if answer:
        ledger.resolve("LIVE-S", answer, resolved_by, T0,
                       message=expired_message(120) if resolved_by == "timeout" else None)
    assert _view(ledger).confirmed_keys == keys
