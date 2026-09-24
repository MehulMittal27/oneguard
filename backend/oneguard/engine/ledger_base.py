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
"""How far back ``LedgerView.priors`` reaches (A3 duplicate window)."""


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
    ) -> LedgerEntry:
        """Close a pending step-up: approve moves reserved → spent, decline releases.

        ``resolved_by="timeout"`` records ``uncertain_outcome="expired"`` (Q2). ``at``
        is the real clock. Raises KeyError if unknown, ValueError if not pending.
        """

    @abstractmethod
    def flag_merchant(self, run_id: str, merchant_id: str, reason: str, at: datetime) -> None:
        """Remember an A1 injection at this shop for later purchases in the run."""


class InMemoryLedger(Ledger):
    """Dict-backed reference ledger for stubs and tests. Not persistent."""

    def __init__(self, session: Any = None, history: HistoryIndex | None = None) -> None:
        super().__init__(session)
        self.history = history
        self.entries: dict[str, LedgerEntry] = {}
        self.flags: dict[str, set[str]] = {}
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

        maxima = [e.billing_amount_chf for e in approved]
        hist_max = self.history.max_approved(customer_id) if self.history else None
        if hist_max is not None:
            maxima.append(hist_max)

        return LedgerView(
            period_spent_chf=round(sum(e.spent_chf for e in in_window), 2),
            period_reserved_chf=round(sum(e.reserved_chf for e in in_window), 2),
            period_window_start=window_start,
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
                )
                for e in sorted(run, key=lambda e: e.ts_sim)
                if e.ts_sim >= at - PRIOR_WINDOW
            ],
            known_merchant_ids=set(on_card) | set(other_cards),
            known_merchant_ids_on_card=set(on_card),
            merchant_approvals_on_card=on_card,
            merchant_approvals_other_cards=other_cards,
            known_device_ids=set(self.history.known_devices(customer_id)) if self.history else set(),
            known_countries=set(self.history.known_countries(customer_id)) if self.history else set(),
            max_approved_chf=max(maxima) if maxima else None,
            flagged_merchant_ids=set(self.flags.get(run_id, set())),
            frozen=False,
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
    ) -> LedgerEntry:
        with self._lock:
            entry = self.entries[authorization_id]
            if entry.outcome != "step_up" or entry.final:
                raise ValueError(f"{authorization_id} is not awaiting an answer")
            if resolved_by == "timeout" and decision != "decline":
                raise ValueError("a timeout only ever declines (rules.md Q2)")
            approved = decision == "approve"
            outcome = "expired" if resolved_by == "timeout" else ("approved" if approved else "declined")
            resolved = entry.model_copy(
                update={
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

    def flag_merchant(self, run_id: str, merchant_id: str, reason: str, at: datetime) -> None:
        with self._lock:
            self.flags.setdefault(run_id, set()).add(merchant_id)
