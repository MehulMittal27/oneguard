"""StoreLedger: the engine's memory, persisted in the store (lane P2).

Implements ``ledger_base.Ledger`` over the ``decisions`` and ``merchant_flags`` tables
(docs/database.md §2). Same semantics as P1's ``InMemoryLedger`` reference, plus the
two things only a stored history can answer: the session watch after an attack and the
customer's remembered confirmations.

Rules it enforces (docs/rules.md):
- M4  only final approvals are spend; declines never count.
- M5  a pending step-up reserves its amount until answered or expired.
- M7  a live ``authorization_id`` is stored once; redelivery returns the stored entry
      and counts nothing (insert-or-return-existing, safe under a concurrent insert).
- Q2  an expired step-up is recorded as ``expired``, declined, and releases its hold.
- C2  the period window is rolling on simulated time: final approvals with
      ``ts_sim >= at - period_days`` and ``ts_sim < at`` (api-contract §3.3), per card.
- Q7  known shops are customer level: history (any card) plus this run's approvals.
- W1, W3 known devices and countries: history plus this run's approvals (their stored
      events); W4 the largest approved purchase, the same way.

Both PM decisions below carry over between sessions for live runs, and stay inside the
run for replays (``runs.kind``), so replaying a scenario always decides the same way.
A run with no ``runs`` row (tests, stubs) is treated like a replay.

Session watch (PM decision; rules.md W-rule 4 pending P1): ``LedgerView.frozen`` is True
after a purchase decided with ``session_trust == "frozen"``, until the customer approves a
step-up. Per card: for a live run, earlier live runs on the same card count too (an
attacker can't clear the watch by starting a new session). ``decide.py`` decides what the
watch does.

Remembered confirmations (PM decision "ask once, then remember"): when the customer
approves a step-up, each of its ``deciding_ids`` is remembered for that shop and those
items, and for that shop whatever the items (``ledger_base.confirmation_keys``). For a
live run, answers from earlier live runs under the same mandate count too; a new or
changed instruction (new mandate) starts with no memory. Exposed as
``LedgerView.confirmed_keys``; ``policy.add_ledger_results`` reads the shop keys only for
a known-shop check (C9) and the item keys only for restrictions no data can check.

Writes commit per call, so a decision is durable before it is posted to Viseca.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oneguard.engine.ledger_base import (
    PRIOR_WINDOW,
    LedgerEntry,
    confirmation_keys,
    is_final_approval,
    known_merchant_names,
)
from oneguard.engine.ledger_base import Ledger as LedgerBase
from oneguard.engine.types import HistoryIndex, LedgerView, PriorDecision
from oneguard.store.schema import Decision, EventRaw, MerchantFlag, Run

CENT = Decimal("0.01")


def _money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _f(x: Any) -> float:
    return float(_money(x)) if x is not None else 0.0


# --- Row <-> entry -----------------------------------------------------------------------
_MONEY_COLUMNS = ("reserved_chf", "spent_chf", "billing_amount_chf")


def _to_row(entry: LedgerEntry) -> Decision:
    data = entry.model_dump(mode="python")
    data["evidence"] = [row.model_dump(mode="json") for row in entry.evidence]
    for col in _MONEY_COLUMNS:
        data[col] = _money(data[col])
    return Decision(**data)


def _to_entry(row: Decision) -> LedgerEntry:
    data = {col.name: getattr(row, col.name) for col in Decision.__table__.columns}
    for col in _MONEY_COLUMNS:
        data[col] = _f(data[col])
    return LedgerEntry.model_validate(data)


class StoreLedger(LedgerBase):
    """The ledger over a SQLAlchemy session (SQLite in tests, Postgres in the cloud)."""

    def __init__(self, session: Session, history: HistoryIndex | None = None) -> None:
        super().__init__(session)
        self.history = history

    # --- reads -------------------------------------------------------------------------
    def get(self, authorization_id: str) -> LedgerEntry | None:
        row = self.session.get(Decision, authorization_id)
        return _to_entry(row) if row is not None else None

    def view(
        self,
        *,
        run_id: str,
        customer_id: str,
        card_id: str,
        at: datetime,
        period_days: int | None,
    ) -> LedgerView:
        window_start = at - timedelta(days=period_days) if period_days else at
        run = list(self.session.scalars(
            select(Decision).where(Decision.run_id == run_id, Decision.ts_sim < at).order_by(Decision.ts_sim)
        ))
        in_window = [d for d in run if d.card_id == card_id and d.ts_sim >= window_start]
        approved = [d for d in run if _money(d.spent_chf) > 0]

        hist_customer = dict(self.history.known_merchants(customer_id)) if self.history else {}
        hist_card = dict(self.history.known_merchants_on_card(card_id)) if self.history else {}
        on_card = dict(hist_card)
        for d in approved:
            if d.card_id == card_id:
                on_card[d.merchant_id] = on_card.get(d.merchant_id, 0) + 1
        other_cards = {m: n - hist_card.get(m, 0) for m, n in hist_customer.items() if n > hist_card.get(m, 0)}
        for d in approved:
            if d.card_id != card_id:
                other_cards[d.merchant_id] = other_cards.get(d.merchant_id, 0) + 1

        maxima = [_f(d.billing_amount_chf) for d in approved]
        hist_max = self.history.max_approved(customer_id) if self.history else None
        if hist_max is not None:
            maxima.append(hist_max)

        flagged = set(self.session.scalars(select(MerchantFlag.merchant_id).where(MerchantFlag.run_id == run_id)))
        run_devices, run_countries = self._approved_devices_and_countries([d.live_authorization_id for d in approved])
        known = set(on_card) | set(other_cards)

        return LedgerView(
            period_spent_chf=float(sum((_money(d.spent_chf) for d in in_window), Decimal(0))),
            period_reserved_chf=float(sum((_money(d.reserved_chf) for d in in_window), Decimal(0))),
            period_window_start=window_start,
            priors=[
                PriorDecision(
                    authorization_id=d.live_authorization_id,
                    timestamp=d.ts_sim,
                    outcome=d.outcome,
                    final=d.final,
                    merchant_id=d.merchant_id,
                    item_ids=list(d.item_ids),
                    billing_amount_chf=_f(d.billing_amount_chf),
                    reserved=_money(d.reserved_chf) > 0,
                    # P1 contract change, P2 to review: approval flag for A3/A4.
                    approved=is_final_approval(d.outcome, d.final, d.uncertain_outcome),
                )
                for d in run
                if d.ts_sim >= at - PRIOR_WINDOW
            ],
            known_merchant_ids=known,
            known_merchant_ids_on_card=set(on_card),
            # P1 contract change, P2 to review: catalogue names of known shops for A7.
            known_merchant_names=known_merchant_names(self.history, known),
            merchant_approvals_on_card=on_card,
            merchant_approvals_other_cards=other_cards,
            known_device_ids=(set(self.history.known_devices(customer_id)) if self.history else set()) | run_devices,
            known_countries=(set(self.history.known_countries(customer_id)) if self.history else set())
            | run_countries,
            max_approved_chf=max(maxima) if maxima else None,
            flagged_merchant_ids=flagged,
            frozen=self._frozen(self._earlier_card_decisions(run_id, card_id)
                                + [d for d in run if d.card_id == card_id]),
            # P1 contract change, P2 to review: the field exists now, so no feature check.
            confirmed_keys=self._confirmed_keys(run_id, at),
        )

    def _approved_devices_and_countries(self, live_ids: list[str]) -> tuple[set[str], set[str]]:
        """Device and shop country of this run's final approvals (W1, W3), read from the
        stored events (``events_raw``, written by the worker and the offline replay before
        the next decision). An approval with no stored event teaches nothing."""
        if not live_ids:
            return set(), set()
        devices: set[str] = set()
        countries: set[str] = set()
        for event in self.session.scalars(select(EventRaw.event).where(EventRaw.live_authorization_id.in_(live_ids))):
            auth = event.get("authorization") or {}
            if auth.get("customer_device_id"):
                devices.add(auth["customer_device_id"])
            if (auth.get("merchant") or {}).get("merchant_country"):
                countries.add(auth["merchant"]["merchant_country"])
        return devices, countries

    @staticmethod
    def _frozen(decisions: list[Decision]) -> bool:
        """Session watch: on after a 'frozen' decision, off once the customer approves a step-up."""
        watch = False
        for d in decisions:  # oldest first
            if d.session_trust == "frozen":
                watch = True
            customer_ok = d.outcome == "step_up" and d.uncertain_outcome == "approved" \
                and d.resolved_by == "customer"
            if watch and customer_ok:  # includes the customer OK-ing the flagged purchase itself
                watch = False
        return watch

    def _earlier_live_runs(self, run_id: str, by: Literal["mandate", "card"]) -> list[str]:
        """Live runs started before this one with the same mandate or card, oldest first.

        Empty for a replay or a run with no ``runs`` row: replays keep their own memory.
        """
        run = self.session.get(Run, run_id)
        if run is None or run.kind != "live":
            return []
        same = Run.mandate_id == run.mandate_id if by == "mandate" else Run.card_id == run.card_id
        return list(self.session.scalars(
            select(Run.run_id)
            .where(Run.kind == "live", same, Run.started_at < run.started_at, Run.run_id != run_id)
            .order_by(Run.started_at)
        ))

    def _earlier_card_decisions(self, run_id: str, card_id: str) -> list[Decision]:
        """This card's decisions in earlier live sessions, in session then time order."""
        runs = self._earlier_live_runs(run_id, "card")
        if not runs:
            return []
        order = {r: i for i, r in enumerate(runs)}
        rows = self.session.scalars(
            select(Decision).where(Decision.run_id.in_(runs), Decision.card_id == card_id)
        )
        return sorted(rows, key=lambda d: (order[d.run_id], d.ts_sim))

    def _confirmed_keys(self, run_id: str, at: datetime) -> set[str]:
        """Answers the customer gave by approving a step-up (ask once, then remember).

        This run before ``at``, plus earlier live sessions under the same mandate.
        """
        customer_ok = (
            Decision.outcome == "step_up",
            Decision.uncertain_outcome == "approved",
            Decision.resolved_by == "customer",
        )
        rows = list(self.session.scalars(select(Decision).where(
            Decision.run_id == run_id, Decision.ts_sim < at, *customer_ok
        )))
        earlier = self._earlier_live_runs(run_id, "mandate")
        if earlier:
            rows += self.session.scalars(select(Decision).where(Decision.run_id.in_(earlier), *customer_ok))
        return {key for d in rows for key in confirmation_keys(d.deciding_ids, d.merchant_id, d.item_ids)}

    # --- writes (each commits: a decision is durable before it is posted) ---------------
    def record(self, entry: LedgerEntry) -> LedgerEntry:
        existing = self.get(entry.live_authorization_id)
        if existing is not None:
            return existing  # M7: redelivery counts nothing
        amount = entry.billing_amount_chf
        stored = entry.model_copy(update={
            "spent_chf": amount if entry.outcome == "approve" else 0.0,
            "reserved_chf": amount if entry.outcome == "step_up" and not entry.final else 0.0,
        })
        row = _to_row(stored)
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError:  # a concurrent insert of the same live id won
            self.session.rollback()
            winner = self.get(entry.live_authorization_id)
            if winner is None:
                raise
            return winner
        return _to_entry(row)  # as stored: money in cents

    def _pending_row(self, authorization_id: str) -> Decision:
        row = self.session.get(Decision, authorization_id)
        if row is None:
            raise KeyError(authorization_id)
        return row

    def reserve(self, authorization_id: str, amount_chf: float) -> None:
        row = self._pending_row(authorization_id)
        row.reserved_chf = _money(amount_chf)
        self.session.commit()

    def release(self, authorization_id: str) -> None:
        row = self._pending_row(authorization_id)
        row.reserved_chf = _money(0)
        self.session.commit()

    def resolve(
        self,
        authorization_id: str,
        decision: Literal["approve", "decline"],
        resolved_by: Literal["customer", "timeout"],
        at: datetime,
    ) -> LedgerEntry:
        row = self._pending_row(authorization_id)
        if row.outcome != "step_up" or row.final:
            raise ValueError(f"{authorization_id} is not awaiting an answer")
        if resolved_by == "timeout" and decision != "decline":
            raise ValueError("a timeout only ever declines (rules.md Q2)")
        approved = decision == "approve"
        row.final = True
        row.uncertain_outcome = "expired" if resolved_by == "timeout" else ("approved" if approved else "declined")
        row.spent_chf = _money(row.billing_amount_chf) if approved else _money(0)
        row.reserved_chf = _money(0)
        row.resolved_at = at
        row.resolved_by = resolved_by
        row.deadline_at = None
        self.session.commit()
        return _to_entry(row)

    def set_deadline(self, authorization_id: str, deadline_at: datetime) -> LedgerEntry:
        """The worker's accepted-time deadline for a pending step-up (api-contract §3.5)."""
        row = self._pending_row(authorization_id)
        if row.outcome != "step_up" or row.final:
            raise ValueError(f"{authorization_id} is not awaiting an answer")
        row.deadline_at = deadline_at
        self.session.commit()
        return _to_entry(row)

    def flag_merchant(self, run_id: str, merchant_id: str, reason: str, at: datetime) -> None:
        self.session.add(MerchantFlag(run_id=run_id, merchant_id=merchant_id, reason=reason, flagged_at=at))
        self.session.commit()


Ledger = StoreLedger
"""The name the worker loads (``viseca.worker.default_ledger``: ``module.Ledger``)."""
