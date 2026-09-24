"""The ledger interface (P1) that P2's ``engine/ledger.py`` implements over the store.

One ``LedgerEntry`` is one row of the ``decisions`` table (docs/database.md §2).
The ledger is the only writer of that table. Rules it enforces (docs/rules.md):
M4 only final approvals are spend, M5 pending step-ups are reserved, M7 redelivery of
a live ``authorization_id`` returns the stored entry and counts nothing, Q2 an expired
step-up is declined and releases its reservation.

``InMemoryLedger`` is the reference used by stubs and tests; it has no session.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from oneguard.engine.types import (
    EvidenceRow,
    HistoryIndex,
    LedgerView,
    Outcome,
    PriorDecision,
    Relation,
    SessionTrust,
)

PRIOR_WINDOW = timedelta(hours=24)
"""How far back ``LedgerView.priors`` reaches (A3 duplicate window). Declines are kept
from the whole run, so a re-quote days later names the declined order (A5)."""


class LedgerEntry(BaseModel):
    """One decision as stored (``decisions`` table). Keys on the live authorization id.

    ``reserved_chf`` and ``spent_chf`` are set by the ledger, not the caller.
    ``deadline_at`` is the real-clock end of the human window on a pending step-up.
    """

    model_config = ConfigDict(extra="forbid")

    live_authorization_id: str
    run_id: str
    mandate_id: str
    card_id: str
    customer_id: str
    ts_sim: AwareDatetime
    outcome: Outcome
    final: bool
    uncertain_outcome: Literal["pending", "expired", "approved", "declined"] | None
    reserved_chf: float = 0.0
    spent_chf: float = 0.0
    merchant_id: str
    item_ids: list[str]
    billing_amount_chf: float
    related_live_id: str | None = None
    relation: Relation | None = None
    session_trust: SessionTrust = "normal"
    step: int = Field(ge=1, le=7)
    deciding_ids: list[str]
    reason_codes: list[str]
    evidence: list[EvidenceRow]
    message: str
    counterfactual: str | None = None
    explanation_source: Literal["template", "model"] = "template"
    injection_flag: dict[str, bool | str] | None = None
    engine_version: str
    latency_ms: float
    signals_enabled: bool
    decided_at: AwareDatetime
    deadline_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    resolved_by: Literal["customer", "timeout"] | None = None


def is_final_approval(outcome: str, final: bool, uncertain_outcome: str | None) -> bool:
    """An approval, or a step-up the customer approved (``PriorDecision.approved``, A3, A4).

    Pending, declined and expired step-ups are not.
    """
    return outcome == "approve" or (final and uncertain_outcome == "approved")


def is_pending(outcome: str, final: bool) -> bool:
    """A step-up still waiting for the customer's answer: reserved, not yet spend (M5)."""
    return outcome == "step_up" and not final


def period_counts(in_window: Iterable[Any]) -> dict[str, Any]:
    """``LedgerView.period_count`` and its companions over the period window's decisions.

    Final approvals plus pending step-ups count; declines and expired step-ups never do
    (M4, M5). Each decision is one live id, so a redelivery counts nothing (M7).
    """
    approved = [d for d in in_window if is_final_approval(d.outcome, d.final, d.uncertain_outcome)]
    pending = [d for d in in_window if is_pending(d.outcome, d.final)]
    return {
        "period_count": len(approved) + len(pending),
        "period_reserved_count": len(pending),
        "period_last_approved_at": max((d.ts_sim for d in approved), default=None),
    }


def check_resolution(
    decision: Literal["approve", "decline"], resolved_by: Literal["customer", "timeout"], message: str | None
) -> None:
    """A timeout only ever declines and always re-renders the message (rules.md Q2);
    a customer's answer keeps the message it was asked with."""
    if resolved_by == "timeout" and decision != "decline":
        raise ValueError("a timeout only ever declines (rules.md Q2)")
    if (resolved_by == "timeout") != (message is not None):
        raise ValueError("a timeout, and only a timeout, replaces the message (rules.md Q2)")


def confirmation_key(rule_id: str, merchant_id: str, item_id: str) -> str:
    """One remembered answer: this rule, at this shop, for this item (``confirmed_keys``)."""
    return f"{rule_id}|{merchant_id}|{item_id}"


def shop_confirmation_key(rule_id: str, merchant_id: str) -> str:
    """One remembered answer for this rule at this shop, whatever the items (C9 known shop)."""
    return f"{rule_id}|{merchant_id}|*"


def confirmation_keys(rule_ids: Iterable[str], merchant_id: str, item_ids: Iterable[str]) -> set[str]:
    """What one step-up the customer approved leaves in ``confirmed_keys``: every deciding
    rule per item and per shop. The reader picks the grain: ``policy.add_ledger_results``
    reads the shop key only for a known-shop check (C9), the item keys only for a
    restriction no data can check."""
    items = list(item_ids)
    keys: set[str] = set()
    for rule_id in rule_ids:
        keys.add(shop_confirmation_key(rule_id, merchant_id))
        keys.update(confirmation_key(rule_id, merchant_id, item_id) for item_id in items)
    return keys


def last_prices(history: HistoryIndex | None, customer_id: str, approved: Iterable[Any]) -> dict[str, float]:
    """``LedgerView.last_price_chf_by_merchant``: history's last approved price at each shop
    the customer knows, replaced by this run's final approvals (``approved``: decisions with
    ``customer_id``, ``merchant_id``, ``ts_sim``, ``billing_amount_chf``), the latest by
    simulated time winning. The run comes after the history it is scored against."""
    prices: dict[str, float] = {}
    if history is not None:
        for merchant_id in history.known_merchants(customer_id):
            price = history.last_price(customer_id, merchant_id)
            if price is not None:
                prices[merchant_id] = float(price)
    for d in sorted((d for d in approved if d.customer_id == customer_id), key=lambda d: d.ts_sim):
        prices[d.merchant_id] = float(d.billing_amount_chf)
    return prices


def known_merchant_names(history: HistoryIndex | None, merchant_ids: set[str]) -> dict[str, str]:
    """Catalogue names of the known merchants (``LedgerView.known_merchant_names``, A7).

    Ids the catalogue does not name are left out, never given an invented name.
    """
    return history.merchant_names(merchant_ids) if history is not None else {}


class Ledger(ABC):
    """Stateful memory of one deployment's decisions. Implementations take a store session."""

    def __init__(self, session: Any) -> None:
        self.session = session

    @abstractmethod
    def get(self, authorization_id: str) -> LedgerEntry | None:
        """The stored entry for this live id, or None (M7)."""

    @abstractmethod
    def record(self, entry: LedgerEntry) -> LedgerEntry:
        """Store a new decision in one transaction and return the stored entry.

        Approve → ``spent_chf`` = amount; step_up → ``reserved_chf`` = amount (M5);
        decline → neither. If the live id is already stored, return the stored entry
        unchanged and count nothing (M7).
        """

    @abstractmethod
    def view(
        self,
        *,
        run_id: str,
        customer_id: str,
        card_id: str,
        at: datetime,
        period_days: int | None,
    ) -> LedgerView:
        """State before a purchase at simulated time ``at`` (C2 rolling window, Q7)."""

    @abstractmethod
    def reserve(self, authorization_id: str, amount_chf: float) -> None:
        """Hold ``amount_chf`` against period limits for a pending step-up (M5)."""

    @abstractmethod
    def release(self, authorization_id: str) -> None:
        """Drop the reservation of a step-up (decline or expiry, M5)."""

    @abstractmethod
    def resolve(
        self,
        authorization_id: str,
        decision: Literal["approve", "decline"],
        resolved_by: Literal["customer", "timeout"],
        at: datetime,
        *,
        message: str | None = None,
    ) -> LedgerEntry:
        """Close a pending step-up: approve moves reserved → spent, decline releases.

        ``resolved_by="timeout"`` records ``uncertain_outcome="expired"`` (Q2) and needs
        ``message`` (``explain.expired_message``): it replaces the stored "Waiting for you"
        message and the counterfactual is dropped; ``explanation_source`` is unchanged. A
        customer's answer keeps the message and takes none. ``at`` is the real clock.
        Raises KeyError if unknown, ValueError if not pending or ``message`` does not fit.
        """

    @abstractmethod
    def flag_merchant(self, run_id: str, merchant_id: str, reason: str, at: datetime) -> None:
        """Remember an A1 injection at this shop for later purchases in the run."""

    def note_event(self, event: dict[str, Any]) -> None:
        """Keep what the view learns from a purchase's event: its device and shop country.

        ``decide_event`` calls this before deciding. The view adds them to
        ``known_device_ids`` / ``known_countries`` once that purchase is a final approval
        (W1, W3); declines, pending and expired step-ups teach nothing. ``StoreLedger``
        reads the stored event (``events_raw``) instead, so the default does nothing.
        """

    def set_deadline(self, authorization_id: str, deadline_at: datetime) -> LedgerEntry:
        """Replace a pending step-up's ``deadline_at`` and return the stored entry.

        The worker calls this once Viseca has accepted the ``step_up``: the deadline is
        the accepted time + the ``/v1/bootstrap`` human window (api-contract §3.5),
        replacing the local default set at record time. Nothing else changes. Raises
        KeyError if unknown, ValueError if not a pending step-up. Not abstract so an
        implementation without it still loads; the worker then keeps the local default.
        """
        raise NotImplementedError


class InMemoryLedger(Ledger):
    """Dict-backed reference ledger for stubs and tests. Not persistent."""

    def __init__(self, session: Any = None, history: HistoryIndex | None = None) -> None:
        super().__init__(session)
        self.history = history
        self.entries: dict[str, LedgerEntry] = {}
        self.flags: dict[str, set[str]] = {}
        self.events: dict[str, tuple[str | None, str | None]] = {}  # live id -> device, country
        self._lock = threading.Lock()

    def get(self, authorization_id: str) -> LedgerEntry | None:
        return self.entries.get(authorization_id)

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        with self._lock:
            existing = self.entries.get(entry.live_authorization_id)
            if existing is not None:
                return existing
            amount = entry.billing_amount_chf
            stored = entry.model_copy(
                update={
                    "spent_chf": amount if entry.outcome == "approve" else 0.0,
                    "reserved_chf": amount if entry.outcome == "step_up" and not entry.final else 0.0,
                }
            )
            self.entries[stored.live_authorization_id] = stored
            return stored

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
        run = [e for e in self.entries.values() if e.run_id == run_id and e.ts_sim < at]
        in_window = [e for e in run if e.card_id == card_id and e.ts_sim >= window_start]
        approved = [e for e in run if e.spent_chf > 0]

        hist_customer = dict(self.history.known_merchants(customer_id)) if self.history else {}
        hist_card = dict(self.history.known_merchants_on_card(card_id)) if self.history else {}
        on_card = dict(hist_card)
        for e in approved:
            if e.card_id == card_id:
                on_card[e.merchant_id] = on_card.get(e.merchant_id, 0) + 1
        other_cards = {
            m: n - hist_card.get(m, 0) for m, n in hist_customer.items() if n > hist_card.get(m, 0)
        }
        for e in approved:
            if e.card_id != card_id:
                other_cards[e.merchant_id] = other_cards.get(e.merchant_id, 0) + 1

        known = set(on_card) | set(other_cards)
        maxima = [e.billing_amount_chf for e in approved]
        hist_max = self.history.max_approved(customer_id) if self.history else None
        if hist_max is not None:
            maxima.append(hist_max)
        noted = [self.events[e.live_authorization_id] for e in approved if e.live_authorization_id in self.events]
        run_devices = {device for device, _ in noted if device}
        run_countries = {country for _, country in noted if country}

        return LedgerView(
            period_spent_chf=round(sum(e.spent_chf for e in in_window), 2),
            period_reserved_chf=round(sum(e.reserved_chf for e in in_window), 2),
            period_window_start=window_start,
            **period_counts(in_window),
            priors=[
                PriorDecision(
                    authorization_id=e.live_authorization_id,
                    timestamp=e.ts_sim,
                    outcome=e.outcome,
                    final=e.final,
                    merchant_id=e.merchant_id,
                    item_ids=e.item_ids,
                    billing_amount_chf=e.billing_amount_chf,
                    reserved=e.reserved_chf > 0,
                    approved=is_final_approval(e.outcome, e.final, e.uncertain_outcome),
                )
                for e in sorted(run, key=lambda e: e.ts_sim)
                if e.ts_sim >= at - PRIOR_WINDOW or e.outcome == "decline"
            ],
            known_merchant_ids=known,
            known_merchant_ids_on_card=set(on_card),
            known_merchant_names=known_merchant_names(self.history, known),
            merchant_approvals_on_card=on_card,
            merchant_approvals_other_cards=other_cards,
            known_device_ids=(set(self.history.known_devices(customer_id)) if self.history else set()) | run_devices,
            known_countries=(set(self.history.known_countries(customer_id)) if self.history else set())
            | run_countries,
            max_approved_chf=max(maxima) if maxima else None,
            last_price_chf_by_merchant=last_prices(self.history, customer_id, approved),
            flagged_merchant_ids=set(self.flags.get(run_id, set())),
            frozen=False,
            confirmed_keys={
                key
                for e in run
                if e.outcome == "step_up" and e.uncertain_outcome == "approved" and e.resolved_by == "customer"
                for key in confirmation_keys(e.deciding_ids, e.merchant_id, e.item_ids)
            },
        )

    def reserve(self, authorization_id: str, amount_chf: float) -> None:
        with self._lock:
            entry = self.entries[authorization_id]
            self.entries[authorization_id] = entry.model_copy(update={"reserved_chf": amount_chf})

    def release(self, authorization_id: str) -> None:
        with self._lock:
            entry = self.entries[authorization_id]
            self.entries[authorization_id] = entry.model_copy(update={"reserved_chf": 0.0})

    def resolve(
        self,
        authorization_id: str,
        decision: Literal["approve", "decline"],
        resolved_by: Literal["customer", "timeout"],
        at: datetime,
        *,
        message: str | None = None,
    ) -> LedgerEntry:
        with self._lock:
            entry = self.entries[authorization_id]
            if entry.outcome != "step_up" or entry.final:
                raise ValueError(f"{authorization_id} is not awaiting an answer")
            check_resolution(decision, resolved_by, message)
            approved = decision == "approve"
            outcome = "expired" if resolved_by == "timeout" else ("approved" if approved else "declined")
            expired = {"message": message, "counterfactual": None} if message is not None else {}
            resolved = entry.model_copy(
                update={
                    **expired,
                    "final": True,
                    "uncertain_outcome": outcome,
                    "reserved_chf": 0.0,
                    "spent_chf": entry.billing_amount_chf if approved else 0.0,
                    "resolved_at": at,
                    "resolved_by": resolved_by,
                    "deadline_at": None,
                }
            )
            self.entries[authorization_id] = resolved
            return resolved

    def note_event(self, event: dict[str, Any]) -> None:
        """Device and shop country per live id, as ``StoreLedger`` reads them from
        ``events_raw``. The first note wins, like the stored event."""
        auth = event.get("authorization") or {}
        with self._lock:
            self.events.setdefault(
                auth["authorization_id"],
                (auth.get("customer_device_id") or None, (auth.get("merchant") or {}).get("merchant_country") or None),
            )

    def flag_merchant(self, run_id: str, merchant_id: str, reason: str, at: datetime) -> None:
        with self._lock:
            self.flags.setdefault(run_id, set()).add(merchant_id)

    def set_deadline(self, authorization_id: str, deadline_at: datetime) -> LedgerEntry:
        with self._lock:
            entry = self.entries[authorization_id]
            if entry.outcome != "step_up" or entry.final:
                raise ValueError(f"{authorization_id} is not awaiting an answer")
            updated = entry.model_copy(update={"deadline_at": deadline_at})
            self.entries[authorization_id] = updated
            return updated
