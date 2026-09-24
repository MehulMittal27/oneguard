"""StoreLedger (lane P2): the engine's memory over the store.

Three layers:
- Unit: every Ledger method against a temp SQLite store and a fake HistoryIndex
  (M4 spend, M5 reserve, M7 redelivery, Q2 timeout, C2 window edges, Q7 known shops,
  session watch, remembered confirmations).
- Parity: random sequences of operations give the same LedgerView as P1's
  InMemoryLedger reference (minus the two fields only StoreLedger computes).
- Pipeline: every public purchase replayed through decide_event with both ledgers
  decides the same way and leaves the same money state.

Test data only: scenario ids are allowed here (not in engine/).
"""
from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from oneguard.engine.explain import expired_message
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import (
    InMemoryLedger,
    LedgerEntry,
    confirmation_key,
    shop_confirmation_key,
)
from oneguard.engine.types import EvidenceRow, Policy
from oneguard.store.db import init_db, make_engine
from oneguard.store.schema import Decision, Run

T = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)  # simulated time of "this purchase"
NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)  # real clock
RUN, CUST, CARD, MANDATE = "run-1", "CU1", "CA1", "TM1"


# --- helpers --------------------------------------------------------------------------


class FakeHistory:
    """The slice of HistoryIndex the ledger reads."""

    def __init__(
        self,
        customer: Mapping[str, int] | None = None,
        card: Mapping[str, int] | None = None,
        devices: frozenset[str] = frozenset(),
        countries: frozenset[str] = frozenset(),
        max_approved: float | None = None,
    ) -> None:
        self._customer, self._card = dict(customer or {}), dict(card or {})
        self._devices, self._countries, self._max = devices, countries, max_approved

    def known_merchants(self, customer_id: str) -> Mapping[str, int]:
        return self._customer if customer_id == CUST else {}

    def known_merchants_on_card(self, card_id: str) -> Mapping[str, int]:
        return self._card if card_id == CARD else {}

    def known_devices(self, customer_id: str) -> frozenset[str]:
        return self._devices

    def known_countries(self, customer_id: str) -> frozenset[str]:
        return self._countries

    def max_approved(self, customer_id: str) -> float | None:
        return self._max

    def merchant_names(self, merchant_ids) -> dict[str, str]:
        return {}  # no catalogue: A7's names are tested in test_ledger_view_contract.py


_seq = iter(range(1, 10**9))


def entry(
    outcome: str = "approve",
    amount: float = 10.0,
    ts: datetime = T - timedelta(hours=1),
    *,
    auth_id: str | None = None,
    run_id: str = RUN,
    card_id: str = CARD,
    merchant: str = "M1",
    items: list[str] | None = None,
    session_trust: str = "normal",
    deciding: list[str] | None = None,
) -> LedgerEntry:
    pending = outcome == "step_up"
    return LedgerEntry(
        live_authorization_id=auth_id or f"LIVE{next(_seq):06d}",
        run_id=run_id, mandate_id=MANDATE, card_id=card_id, customer_id=CUST,
        ts_sim=ts, outcome=outcome, final=not pending,
        uncertain_outcome="pending" if pending else None,
        merchant_id=merchant, item_ids=items if items is not None else ["I1"],
        billing_amount_chf=amount, session_trust=session_trust, step=5,
        deciding_ids=deciding if deciding is not None else ["C1"], reason_codes=["TEST"],
        evidence=[EvidenceRow(rule="C1", outcome="pass", detail="d", source="policy")],
        message="m", engine_version="test", latency_ms=1.0, signals_enabled=False,
        decided_at=NOW, deadline_at=NOW + timedelta(minutes=5) if pending else None,
    )  # fmt: skip


@pytest.fixture
def maker(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def ledger(maker: sessionmaker[Session]) -> Iterator[StoreLedger]:
    with maker() as s:
        yield StoreLedger(s)


def view(led, at: datetime = T, period_days: int | None = 7, card_id: str = CARD, run_id: str = RUN):
    return led.view(run_id=run_id, customer_id=CUST, card_id=card_id, at=at, period_days=period_days)


def rows(maker: sessionmaker[Session]) -> int:
    with maker() as s:
        return s.scalar(select(func.count()).select_from(Decision))


# --- M4 / M5: what counts as money ----------------------------------------------------


def test_approve_is_spend(ledger):
    stored = ledger.record(entry("approve", 42.5))
    assert (stored.spent_chf, stored.reserved_chf) == (42.5, 0.0)
    assert (view(ledger).period_spent_chf, view(ledger).period_reserved_chf) == (42.5, 0.0)


def test_pending_step_up_is_reserved_not_spent(ledger):
    stored = ledger.record(entry("step_up", 65.0))
    assert (stored.spent_chf, stored.reserved_chf) == (0.0, 65.0)
    v = view(ledger)
    assert (v.period_spent_chf, v.period_reserved_chf) == (0.0, 65.0)
    assert v.priors[-1].reserved is True


def test_decline_counts_nothing(ledger):
    stored = ledger.record(entry("decline", 99.0))
    assert (stored.spent_chf, stored.reserved_chf) == (0.0, 0.0)
    v = view(ledger)
    assert (v.period_spent_chf, v.period_reserved_chf) == (0.0, 0.0)
    assert v.max_approved_chf is None
    assert v.known_merchant_ids == set()


def test_caller_cannot_set_money(ledger):
    e = entry("decline", 50.0).model_copy(update={"spent_chf": 50.0, "reserved_chf": 50.0})
    stored = ledger.record(e)
    assert (stored.spent_chf, stored.reserved_chf) == (0.0, 0.0)


def test_answered_step_up_holds_nothing(ledger):
    e = entry("step_up", 65.0).model_copy(update={"final": True, "uncertain_outcome": "declined"})
    assert ledger.record(e).reserved_chf == 0.0
    assert view(ledger).period_reserved_chf == 0.0


def test_money_rounds_to_cents_half_even(ledger):
    stored = ledger.record(entry("approve", 2.675))  # the float is 2.67499…; the amount is 2.675
    assert (stored.billing_amount_chf, stored.spent_chf) == (2.68, 2.68)
    assert view(ledger).period_spent_chf == 2.68


def test_money_is_exact_in_cents(ledger):
    for amount in (0.1, 0.2, 33.33, 33.33, 33.34):
        ledger.record(entry("approve", amount))
    assert view(ledger).period_spent_chf == 100.30


# --- M7: redelivery ------------------------------------------------------------------


def test_redelivery_returns_first_entry_and_counts_once(ledger, maker):
    first = ledger.record(entry("approve", 20.0, auth_id="LIVE_A"))
    again = ledger.record(entry("decline", 999.0, auth_id="LIVE_A"))
    assert again == first
    assert rows(maker) == 1
    assert view(ledger).period_spent_chf == 20.0


def test_concurrent_insert_returns_the_winner(maker):
    with maker() as a, maker() as b:
        la, lb = StoreLedger(a), StoreLedger(b)
        winner = la.record(entry("approve", 20.0, auth_id="LIVE_RACE"))
        real_get, looks = lb.get, []

        def racing_get(auth_id):  # b's first look happened before a's insert landed
            looks.append(auth_id)
            return None if len(looks) == 1 else real_get(auth_id)

        lb.get = racing_get
        loser = lb.record(entry("step_up", 50.0, auth_id="LIVE_RACE"))
        assert loser.outcome == "approve" and loser.spent_chf == winner.spent_chf
    assert rows(maker) == 1


def test_persists_across_sessions(maker):
    with maker() as s:
        StoreLedger(s).record(entry("step_up", 30.0, auth_id="LIVE_P"))
    with maker() as s:
        led = StoreLedger(s)
        got = led.get("LIVE_P")
        assert got is not None and got.reserved_chf == 30.0 and got.uncertain_outcome == "pending"
        assert got.evidence[0].rule == "C1"
        assert view(led).period_reserved_chf == 30.0


def test_get_unknown_is_none(ledger):
    assert ledger.get("NOPE") is None


# --- resolve / reserve / release (M5, Q2) ---------------------------------------------


def test_customer_approves_step_up(ledger):
    ledger.record(entry("step_up", 65.0, auth_id="S1"))
    r = ledger.resolve("S1", "approve", "customer", NOW)
    assert (r.final, r.uncertain_outcome, r.resolved_by) == (True, "approved", "customer")
    assert (r.spent_chf, r.reserved_chf, r.deadline_at) == (65.0, 0.0, None)
    v = view(ledger)
    assert (v.period_spent_chf, v.period_reserved_chf) == (65.0, 0.0)
    assert "M1" in v.known_merchant_ids


def test_customer_declines_step_up(ledger):
    ledger.record(entry("step_up", 65.0, auth_id="S1"))
    r = ledger.resolve("S1", "decline", "customer", NOW)
    assert (r.uncertain_outcome, r.spent_chf, r.reserved_chf) == ("declined", 0.0, 0.0)
    assert (view(ledger).period_spent_chf, view(ledger).period_reserved_chf) == (0.0, 0.0)


def test_timeout_is_expired_and_declined(ledger):
    ledger.record(entry("step_up", 65.0, auth_id="S1"))
    r = ledger.resolve("S1", "decline", "timeout", NOW, message=expired_message(120))
    assert (r.uncertain_outcome, r.resolved_by, r.spent_chf, r.reserved_chf) == ("expired", "timeout", 0.0, 0.0)


def test_timeout_can_never_approve(ledger):
    ledger.record(entry("step_up", 65.0, auth_id="S1"))
    with pytest.raises(ValueError):
        ledger.resolve("S1", "approve", "timeout", NOW, message=expired_message(120))
    assert ledger.get("S1").reserved_chf == 65.0  # nothing changed


@pytest.mark.parametrize("outcome", ["approve", "decline"])
def test_only_pending_step_ups_resolve(ledger, outcome):
    ledger.record(entry(outcome, 10.0, auth_id="X"))
    with pytest.raises(ValueError):
        ledger.resolve("X", "approve", "customer", NOW)


def test_resolve_twice_fails(ledger):
    ledger.record(entry("step_up", 10.0, auth_id="S1"))
    ledger.resolve("S1", "approve", "customer", NOW)
    with pytest.raises(ValueError):
        ledger.resolve("S1", "decline", "customer", NOW)
    assert view(ledger).period_spent_chf == 10.0


@pytest.mark.parametrize("call", ["resolve", "reserve", "release"])
def test_unknown_id_is_key_error(ledger, call):
    args = {"resolve": ("NOPE", "approve", "customer", NOW), "reserve": ("NOPE", 1.0), "release": ("NOPE",)}
    with pytest.raises(KeyError):
        getattr(ledger, call)(*args[call])


def test_set_deadline_moves_only_the_deadline(ledger, maker):
    ledger.record(entry("step_up", 40.0, auth_id="S1"))
    later = NOW + timedelta(minutes=9)
    got = ledger.set_deadline("S1", later)
    assert (got.deadline_at, got.reserved_chf, got.uncertain_outcome) == (later, 40.0, "pending")
    with maker() as s:
        assert StoreLedger(s).get("S1").deadline_at == later  # stored, not just returned


def test_set_deadline_only_on_a_pending_ask(ledger):
    ledger.record(entry("approve", 10.0, auth_id="A"))
    ledger.record(entry("step_up", 10.0, auth_id="S"))
    ledger.resolve("S", "decline", "timeout", NOW, message=expired_message(120))
    for auth_id, error in (("A", ValueError), ("S", ValueError), ("NOPE", KeyError)):
        with pytest.raises(error):
            ledger.set_deadline(auth_id, NOW)


def test_the_worker_finds_this_ledger():
    from oneguard.engine import ledger as module

    assert module.Ledger is StoreLedger


def test_reserve_and_release(ledger):
    ledger.record(entry("step_up", 40.0, auth_id="S1"))
    ledger.reserve("S1", 12.5)
    assert view(ledger).period_reserved_chf == 12.5
    ledger.release("S1")
    assert view(ledger).period_reserved_chf == 0.0


# --- C2: the rolling window -----------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "counted"),
    [
        (timedelta(days=7), True),  # exactly 7 days before: inside (ts >= at - 7d)
        (timedelta(days=7, seconds=1), False),  # one second older: outside
        (timedelta(seconds=1), True),
        (timedelta(0), False),  # same instant: not "before" this purchase
        (-timedelta(hours=1), False),  # later in simulated time
    ],
    ids=["7d-edge-in", "7d+1s-out", "1s-before", "same-instant", "after"],
)
def test_window_edges(ledger, offset, counted):
    ledger.record(entry("approve", 50.0, T - offset))
    assert view(ledger).period_spent_chf == (50.0 if counted else 0.0)


def test_window_follows_period_days(ledger):
    ledger.record(entry("approve", 50.0, T - timedelta(days=20)))
    assert view(ledger, period_days=7).period_spent_chf == 0.0
    assert view(ledger, period_days=30).period_spent_chf == 50.0
    assert view(ledger, period_days=30).period_window_start == T - timedelta(days=30)


def test_no_period_rule_means_no_window(ledger):
    ledger.record(entry("approve", 50.0))
    v = view(ledger, period_days=None)
    assert (v.period_spent_chf, v.period_window_start) == (0.0, T)


def test_window_is_per_card_and_per_run(ledger):
    ledger.record(entry("approve", 10.0))
    ledger.record(entry("approve", 20.0, card_id="CA2"))
    ledger.record(entry("approve", 40.0, run_id="run-2"))
    assert view(ledger).period_spent_chf == 10.0
    assert view(ledger, card_id="CA2").period_spent_chf == 20.0
    assert view(ledger, run_id="run-2").period_spent_chf == 40.0


def test_grocery_week_spent_plus_reserved(ledger):
    """M5 walk-through: three approvals, one pending ask, one decline in the week."""
    for amount, hours in ((120.0, 100), (80.0, 60), (34.5, 30)):
        ledger.record(entry("approve", amount, T - timedelta(hours=hours)))
    ledger.record(entry("step_up", 65.0, T - timedelta(hours=10), auth_id="ASK"))
    ledger.record(entry("decline", 300.0, T - timedelta(hours=5)))
    v = view(ledger)
    assert (v.period_spent_chf, v.period_reserved_chf) == (234.5, 65.0)
    ledger.resolve("ASK", "decline", "timeout", NOW, message=expired_message(120))
    assert (view(ledger).period_spent_chf, view(ledger).period_reserved_chf) == (234.5, 0.0)


# --- what the view knows (priors, Q7, W1/W3/W4, A1) -----------------------------------


def test_priors_cover_24h_oldest_first(ledger):
    ledger.record(entry("approve", 1.0, T - timedelta(hours=2), auth_id="B"))
    ledger.record(entry("decline", 1.0, T - timedelta(hours=23), auth_id="A"))
    ledger.record(entry("approve", 1.0, T - timedelta(hours=25), auth_id="OLD"))
    assert [p.authorization_id for p in view(ledger).priors] == ["A", "B"]


def test_known_shops_are_customer_level(maker):
    hist = FakeHistory(customer={"M_HIST": 3, "M_BOTH": 2}, card={"M_BOTH": 1})
    with maker() as s:
        led = StoreLedger(s, hist)
        led.record(entry("approve", 5.0, merchant="M_RUN"))
        led.record(entry("approve", 5.0, merchant="M_OTHER", card_id="CA2"))
        led.record(entry("decline", 5.0, merchant="M_DECLINED"))
        led.record(entry("step_up", 5.0, merchant="M_PENDING"))
        v = view(led)
    assert v.known_merchant_ids == {"M_HIST", "M_BOTH", "M_RUN", "M_OTHER"}
    assert v.known_merchant_ids_on_card == {"M_BOTH", "M_RUN"}
    assert v.merchant_approvals_on_card == {"M_BOTH": 1, "M_RUN": 1}
    assert v.merchant_approvals_other_cards == {"M_HIST": 3, "M_BOTH": 1, "M_OTHER": 1}


def test_devices_countries_and_largest_purchase(maker):
    hist = FakeHistory(devices=frozenset({"D1"}), countries=frozenset({"CH"}), max_approved=80.0)
    with maker() as s:
        led = StoreLedger(s, hist)
        assert view(led).max_approved_chf == 80.0
        led.record(entry("approve", 120.0))
        led.record(entry("decline", 900.0))
        v = view(led)
    assert (v.known_device_ids, v.known_countries, v.max_approved_chf) == ({"D1"}, {"CH"}, 120.0)


def test_flagged_shops_stay_in_their_run(ledger):
    ledger.flag_merchant(RUN, "M_BAD", "injection", NOW)
    assert view(ledger).flagged_merchant_ids == {"M_BAD"}
    assert view(ledger, run_id="run-2").flagged_merchant_ids == set()


# --- session watch (PM decision) -------------------------------------------------------


def _resolve(ledger, auth_id, answer, by):
    """A customer's answer, or the timeout with its re-rendered message (rules.md Q2)."""
    ledger.resolve(auth_id, answer, by, NOW, message=expired_message(120) if by == "timeout" else None)


def _walk(ledger, steps):
    """steps: (outcome, session_trust, resolve_with) in time order."""
    for i, (outcome, trust, answer) in enumerate(steps):
        e = entry(outcome, 5.0, T - timedelta(hours=10 - i), session_trust=trust)
        ledger.record(e)
        if answer:
            _resolve(ledger, e.live_authorization_id, *answer)
    return view(ledger).frozen


@pytest.mark.parametrize(
    ("steps", "frozen"),
    [
        ([("approve", "normal", None)], False),
        ([("step_up", "frozen", None)], True),
        ([("step_up", "frozen", None), ("approve", "normal", None)], True),
        ([("step_up", "frozen", ("approve", "customer"))], False),
        ([("step_up", "frozen", None), ("step_up", "normal", ("approve", "customer"))], False),
        ([("step_up", "frozen", None), ("step_up", "normal", ("decline", "customer"))], True),
        ([("step_up", "frozen", None), ("step_up", "normal", ("decline", "timeout"))], True),
        ([("step_up", "frozen", ("approve", "customer")), ("decline", "frozen", None)], True),
    ],
    ids=["never", "attack", "stays-on", "ok-clears", "later-ok-clears", "no-keeps", "timeout-keeps", "re-arms"],
)
def test_session_watch(ledger, steps, frozen):
    assert _walk(ledger, steps) is frozen


def test_session_watch_is_per_card_and_before_this_purchase(ledger):
    ledger.record(entry("step_up", 5.0, session_trust="frozen", card_id="CA2"))
    ledger.record(entry("step_up", 5.0, T + timedelta(hours=1), session_trust="frozen"))
    assert view(ledger).frozen is False
    assert view(ledger, card_id="CA2").frozen is True


# --- ask once, then remember (PM decision) ---------------------------------------------


def _ask(ledger, answer, *, run_id=RUN, ts=T - timedelta(hours=1), rules=("U1",)):
    e = entry("step_up", 5.0, ts, run_id=run_id, merchant="GYM", items=["I1", "I2"], deciding=list(rules))
    ledger.record(e)
    if answer:
        _resolve(ledger, e.live_authorization_id, *answer)


def test_customer_ok_is_remembered_per_rule_shop_and_item(ledger):
    _ask(ledger, ("approve", "customer"), rules=("U1", "U2"))
    assert ledger._confirmed_keys(RUN, T) == {
        confirmation_key(r, "GYM", i) for r in ("U1", "U2") for i in ("I1", "I2")
    } | {shop_confirmation_key(r, "GYM") for r in ("U1", "U2")}
    assert confirmation_key("U1", "GYM", "I1") == "U1|GYM|I1"
    assert shop_confirmation_key("U1", "GYM") == "U1|GYM|*"


@pytest.mark.parametrize(
    "answer", [None, ("decline", "customer"), ("decline", "timeout")], ids=["pending", "no", "timeout"]
)
def test_only_a_customer_ok_is_remembered(ledger, answer):
    _ask(ledger, answer)
    assert ledger._confirmed_keys(RUN, T) == set()


def test_memory_without_a_run_row_stays_in_its_run_and_its_past(ledger):
    _ask(ledger, ("approve", "customer"), run_id="run-2")
    _ask(ledger, ("approve", "customer"), ts=T + timedelta(hours=1))
    assert ledger._confirmed_keys(RUN, T) == set()


def test_view_carries_confirmations(ledger):
    _ask(ledger, ("approve", "customer"))
    assert view(ledger).confirmed_keys == {"U1|GYM|I1", "U1|GYM|I2", "U1|GYM|*"}


# --- carrying over between sessions (live runs only) -----------------------------------


def add_run(led, run_id, kind="live", *, mandate=MANDATE, card=CARD, day=0):
    led.session.add(Run(run_id=run_id, kind=kind, mandate_id=mandate, card_id=card,
                        state="finished", started_at=NOW + timedelta(days=day)))
    led.session.commit()


def _ask_in(led, run_id, answer, *, trust="normal", card=CARD, rules=("U1",)):
    e = entry("step_up", 5.0, T - timedelta(hours=2), run_id=run_id, card_id=card, merchant="GYM",
              items=["I1"], deciding=list(rules), session_trust=trust)
    led.record(e)
    if answer:
        _resolve(led, e.live_authorization_id, *answer)


def test_live_memory_carries_to_the_next_session(ledger):
    add_run(ledger, "live-1", day=0)
    add_run(ledger, "live-2", day=1)
    _ask_in(ledger, "live-1", ("approve", "customer"))
    assert ledger._confirmed_keys("live-2", T - timedelta(days=30)) == {"U1|GYM|I1", "U1|GYM|*"}


@pytest.mark.parametrize(
    ("first", "second", "carried"),
    [
        (("replay", MANDATE), ("replay", MANDATE), False),  # replays keep their own memory
        (("replay", MANDATE), ("live", MANDATE), False),  # a test run never teaches live
        (("live", MANDATE), ("replay", MANDATE), False),  # nor the other way round
        (("live", "TM_OLD"), ("live", MANDATE), False),  # changed instruction: start fresh
        (("live", MANDATE), ("live", MANDATE), True),
    ],
    ids=["replay-replay", "replay-live", "live-replay", "new-instruction", "live-live"],
)
def test_which_sessions_share_memory(ledger, first, second, carried):
    add_run(ledger, "r1", first[0], mandate=first[1], day=0)
    add_run(ledger, "r2", second[0], mandate=second[1], day=1)
    _ask_in(ledger, "r1", ("approve", "customer"))
    assert (ledger._confirmed_keys("r2", T) == {"U1|GYM|I1", "U1|GYM|*"}) is carried


def test_memory_never_comes_from_a_later_session(ledger):
    add_run(ledger, "live-1", day=0)
    add_run(ledger, "live-2", day=1)
    _ask_in(ledger, "live-2", ("approve", "customer"))
    assert ledger._confirmed_keys("live-1", T) == set()


@pytest.mark.parametrize("answer", [None, ("decline", "customer"), ("decline", "timeout")])
def test_only_a_yes_carries_over(ledger, answer):
    add_run(ledger, "live-1", day=0)
    add_run(ledger, "live-2", day=1)
    _ask_in(ledger, "live-1", answer)
    assert ledger._confirmed_keys("live-2", T) == set()


def test_watch_carries_to_the_next_live_session_on_the_card(ledger):
    add_run(ledger, "live-1", day=0)
    add_run(ledger, "live-2", mandate="TM_NEW", day=1)  # a new instruction doesn't clear an attack
    _ask_in(ledger, "live-1", None, trust="frozen")
    assert view(ledger, run_id="live-2").frozen is True
    _ask_in(ledger, "live-2", ("approve", "customer"))  # customer says yes in the new session
    assert view(ledger, run_id="live-2").frozen is False


def test_yes_in_the_old_session_clears_it_for_the_next(ledger):
    add_run(ledger, "live-1", day=0)
    add_run(ledger, "live-2", day=1)
    _ask_in(ledger, "live-1", ("approve", "customer"), trust="frozen")
    assert view(ledger, run_id="live-2").frozen is False


@pytest.mark.parametrize(
    ("first_kind", "second_kind", "first_card"),
    [("replay", "replay", CARD), ("live", "replay", CARD), ("live", "live", "CA2")],
    ids=["replays", "into-replay", "other-card"],
)
def test_watch_does_not_carry(ledger, first_kind, second_kind, first_card):
    add_run(ledger, "r1", first_kind, card=first_card, day=0)
    add_run(ledger, "r2", second_kind, day=1)
    _ask_in(ledger, "r1", None, trust="frozen", card=first_card)
    assert view(ledger, run_id="r2").frozen is False


def test_replaying_twice_decides_the_same(ledger):
    """The reason replays keep their own memory: the second replay sees what the first saw."""
    views = []
    for i, run_id in enumerate(("replay-1", "replay-2")):
        add_run(ledger, run_id, "replay", day=i)
        before = view(ledger, run_id=run_id)
        views.append((before.frozen, ledger._confirmed_keys(run_id, T)))
        _ask_in(ledger, run_id, ("approve", "customer"), trust="frozen")
    assert views[0] == views[1] == (False, set())


# --- parity with P1's InMemoryLedger ---------------------------------------------------

ONLY_STORE = {"frozen", "confirmed_keys"}


def _comparable(v) -> dict:
    return {k: val for k, val in v.model_dump().items() if k not in ONLY_STORE}


@pytest.mark.parametrize("seed", range(40))
def test_same_view_as_reference(maker, seed):
    rnd = random.Random(seed)
    hist = FakeHistory(customer={"M1": 2, "M9": 1}, card={"M1": 1}, max_approved=rnd.choice([None, 55.0]))
    ref = InMemoryLedger(history=hist)
    with maker() as s:
        led = StoreLedger(s, hist)
        pending: list[str] = []
        for _ in range(40):
            op = rnd.random()
            if op < 0.7 or not pending:
                e = entry(
                    rnd.choice(["approve", "approve", "decline", "step_up"]),
                    round(rnd.uniform(1, 300), 2),
                    T - timedelta(minutes=rnd.randint(0, 60 * 24 * 10)),
                    run_id=rnd.choice([RUN, RUN, "run-2"]), card_id=rnd.choice([CARD, CARD, "CA2"]),
                    merchant=rnd.choice(["M1", "M2", "M3", "M9"]),
                )  # fmt: skip
                assert led.record(e) == ref.record(e)
                if e.outcome == "step_up":
                    pending.append(e.live_authorization_id)
            elif op < 0.9:
                auth = pending.pop(rnd.randrange(len(pending)))
                answer = rnd.choice([("approve", "customer"), ("decline", "customer"), ("decline", "timeout")])
                message = expired_message(120) if answer[1] == "timeout" else None
                assert led.resolve(auth, *answer, NOW, message=message) == ref.resolve(auth, *answer, NOW, message=message)
            else:
                m = rnd.choice(["M1", "M2"])
                led.flag_merchant(RUN, m, "x", NOW)
                ref.flag_merchant(RUN, m, "x", NOW)
            at = T - timedelta(minutes=rnd.randint(0, 60 * 24 * 3))
            days = rnd.choice([None, 1, 7, 30])
            for run_id in (RUN, "run-2"):
                for card in (CARD, "CA2"):
                    assert _comparable(view(led, at, days, card, run_id)) == _comparable(
                        view(ref, at, days, card, run_id)
                    ), (seed, run_id, card, at, days)


# --- pipeline: every public purchase with both ledgers ---------------------------------

POLICIES = Path(__file__).parent / "fixtures" / "policies"


@pytest.fixture(scope="module")
def pack_and_history(tmp_path_factory: pytest.TempPathFactory):
    from oneguard.replay.events import Pack
    from oneguard.store import seed as seed_module
    from oneguard.store.db import session
    from oneguard.store.history import StoreHistoryIndex

    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('ledger') / 'oneguard.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        history = StoreHistoryIndex.load(s)
    engine.dispose()
    return Pack.load(), history


def _replay(pack, history, scenario_id: str, ledger, implementations=None) -> list[tuple]:
    """Replay a scenario; every ask is answered alternately yes / no.

    Default: every engine function stubbed (every purchase asks), so this checks the
    ledger's money alone, independent of decide."""
    from oneguard.engine import stubs
    from oneguard.pipeline import PipelineContext, decide_event
    from oneguard.replay.events import build_events

    impl = dict(stubs.STUBS) if implementations is None else implementations

    fixture = yaml.safe_load((POLICIES / f"{scenario_id}.yaml").read_text(encoding="utf-8"))
    fixture.pop("scenario_id")
    policy = Policy.model_validate({"mandate_id": f"TM_LEDGER_{scenario_id}", **fixture})
    ctx = PipelineContext(policy=policy, ledger=ledger, history=history, run_id=f"ledger-{scenario_id}",
                          implementations=impl, stubbed=frozenset())
    out, flip = [], False
    for event in build_events(pack, scenario_id, mandate_id=policy.mandate_id, now=NOW):
        engine, _, _ = decide_event(event, ctx)
        decide_event(event, ctx)  # redelivery: must change nothing
        auth_id = event["authorization"]["authorization_id"]
        if engine.outcome == "step_up":  # answer alternately yes / no
            flip = not flip
            ledger.resolve(auth_id, "approve" if flip else "decline", "customer", NOW)
        stored = ledger.get(auth_id)
        out.append((auth_id, engine.outcome, engine.step, stored.spent_chf, stored.reserved_chf))
    return out


def test_pipeline_parity_on_the_data_pack(pack_and_history, maker):
    pack, history = pack_and_history
    for scenario_id in pack.scenario_ids():
        with maker() as s:
            store = _replay(pack, history, scenario_id, StoreLedger(s, history))
        ref = _replay(pack, history, scenario_id, InMemoryLedger(history=history))
        assert store == ref, scenario_id
    assert rows(maker) == sum(len(pack.attempts_for(sid)) for sid in pack.scenario_ids())


def test_session_watch_on_the_data_pack(pack_and_history, maker):
    """PM decision M1 with the real engine (facts, policy, P5's signals, decide): after the
    SCEN0003 burst, the first clean purchase asks instead of approving; a "no" keeps the
    watch on, so the next clean purchase asks too. InMemoryLedger has no watch, so there
    both approve (rules.md W-rule 4, pending P1)."""
    from oneguard.engine import stubs

    pack, history = pack_and_history
    real = dict(stubs.ACTIVE)
    with maker() as s:
        store = _replay(pack, history, "SCEN0003", StoreLedger(s, history), real)
    ref = _replay(pack, history, "SCEN0003", InMemoryLedger(history=history), real)
    changed = [(a, b) for a, b in zip(store, ref, strict=True) if a[1] != b[1]]
    assert [(a[1], b[1]) for a, b in changed] == [("step_up", "approve")] * len(changed)
    assert 1 <= len(changed) <= 2
    first, second = (changed + [None])[:2]
    order = [r[0] for r in store]
    if second is not None:  # asked again only because the customer said no the first time
        assert order.index(second[0][0]) == order.index(first[0][0]) + 1
