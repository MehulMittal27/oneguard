"""Store reads and writes behind ``/api`` (docs/database.md §2). Synchronous: routes run
them in a thread through ``Services.db``.

The ``decisions`` table is only read here; P2's ledger is its one writer. Reference
tables are read-only at runtime. ``policy_drafts``, ``mandates`` and ``runs`` (replay
rows) are written here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from oneguard.engine.ledger_base import LedgerEntry
from oneguard.store.db import session
from oneguard.store.schema import (
    Account,
    Card,
    Customer,
    Decision,
    EventRaw,
    Mandate,
    PolicyDraft,
    RequestedItemOrder,
    Run,
    ScenarioCatalogue,
    ScenarioProfile,
    WorkerState,
)

SERVED_SCENARIOS_KEY = "served_scenarios"
"""``worker_state`` row the worker writes at start (``viseca.worker.SERVED_SCENARIOS_KEY``)."""

_CENT = Decimal("0.01")
_MONEY_COLUMNS = ("reserved_chf", "spent_chf", "billing_amount_chf")


def money(value: Any) -> float:
    """A stored amount as float CHF, half-even to the cent (M2)."""
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_EVEN))


def entry_of(row: Decision) -> LedgerEntry:
    """One ``decisions`` row as the ledger's ``LedgerEntry``."""
    data = {col.name: getattr(row, col.name) for col in Decision.__table__.columns}
    for col in _MONEY_COLUMNS:
        data[col] = money(data[col])
    return LedgerEntry.model_validate(data)


@dataclass(frozen=True)
class StoredDecision:
    """A decision with the event it decided and the kind and start of the run it belongs to."""

    entry: LedgerEntry
    event: dict[str, Any] | None
    run_kind: str | None
    run_started_at: datetime | None = None


# Reference data -------------------------------------------------------------------------


def customers(db: Engine) -> list[Customer]:
    with session(db) as s:
        return list(s.scalars(select(Customer).order_by(Customer.customer_id)))


def customer_exists(db: Engine, customer_id: str) -> bool:
    with session(db) as s:
        return s.get(Customer, customer_id) is not None


def accounts(db: Engine, customer_id: str) -> list[tuple[Account, list[Card]]]:
    with session(db) as s:
        rows = list(
            s.scalars(
                select(Account).where(Account.customer_id == customer_id).order_by(Account.account_id)
            )
        )
        cards = list(
            s.scalars(
                select(Card)
                .where(Card.account_id.in_([a.account_id for a in rows]))
                .order_by(Card.card_id)
            )
        )
    return [(a, [c for c in cards if c.account_id == a.account_id]) for a in rows]


def card_customer(db: Engine, card_id: str) -> str | None:
    """The customer who holds ``card_id``, or None if the card is unknown."""
    with session(db) as s:
        return s.scalar(
            select(Account.customer_id)
            .join(Card, Card.account_id == Account.account_id)
            .where(Card.card_id == card_id)
        )


def customer_names(db: Engine, customer_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted(set(customer_ids))
    if not ids:
        return {}
    with session(db) as s:
        rows = s.execute(select(Customer.customer_id, Customer.persona_name).where(Customer.customer_id.in_(ids)))
        return {r[0]: r[1] for r in rows}


# Scenarios (operator data: routes_dev and C12 only) --------------------------------------


def scenario_catalogue(db: Engine) -> list[ScenarioCatalogue]:
    with session(db) as s:
        return list(s.scalars(select(ScenarioCatalogue).order_by(ScenarioCatalogue.scenario_id)))


def scenario_profiles(db: Engine) -> list[ScenarioProfile]:
    """The platform's scenario → customer / card bindings the worker stored."""
    with session(db) as s:
        return list(s.scalars(select(ScenarioProfile).order_by(ScenarioProfile.scenario_id)))


def served_scenarios(db: Engine) -> list[str] | None:
    """The scenario ids the platform served at the worker's last start; None if it never read them."""
    with session(db) as s:
        row = s.get(WorkerState, SERVED_SCENARIOS_KEY)
    if row is None or not isinstance(row.value, list):
        return None
    return [v for v in row.value if isinstance(v, str)]


# Decisions ------------------------------------------------------------------------------


def _runs(s: Session, run_ids: Iterable[str]) -> dict[str, tuple[str, datetime]]:
    """``run_id`` → (kind, started_at) for the runs that have a ``runs`` row."""
    ids = sorted(set(run_ids))
    if not ids:
        return {}
    rows = s.execute(select(Run.run_id, Run.kind, Run.started_at).where(Run.run_id.in_(ids))).all()
    return {run_id: (kind, started_at) for run_id, kind, started_at in rows}


def _stored(entry: LedgerEntry, event: dict[str, Any] | None, run: tuple[str, datetime] | None) -> StoredDecision:
    kind, started_at = run if run is not None else (None, None)
    return StoredDecision(entry, event, kind, started_at)


def _events(s: Session, live_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = sorted(set(live_ids))
    if not ids:
        return {}
    rows = s.execute(
        select(EventRaw.live_authorization_id, EventRaw.event).where(
            EventRaw.live_authorization_id.in_(ids)
        )
    ).all()
    return dict(rows)


def customer_decisions(db: Engine, customer_id: str) -> list[StoredDecision]:
    """Every decision for this customer, newest simulated time first."""
    with session(db) as s:
        rows = list(
            s.scalars(
                select(Decision)
                .where(Decision.customer_id == customer_id)
                .order_by(Decision.ts_sim.desc(), Decision.decided_at.desc())
            )
        )
        entries = [entry_of(r) for r in rows]
        events = _events(s, (e.live_authorization_id for e in entries))
        runs = _runs(s, (e.run_id for e in entries))
    return [_stored(e, events.get(e.live_authorization_id), runs.get(e.run_id)) for e in entries]


def decision(db: Engine, live_id: str) -> StoredDecision | None:
    with session(db) as s:
        row = s.get(Decision, live_id)
        if row is None:
            return None
        entry = entry_of(row)
        event = _events(s, [live_id]).get(live_id)
        run = _runs(s, [entry.run_id]).get(entry.run_id)
    return _stored(entry, event, run)


def latest_run_decisions(db: Engine, *, card_id: str) -> list[LedgerEntry]:
    """The decisions of the most recent run on this card, whichever mandate decided them.

    "Most recent" is the run of the decision decided last on the real clock.
    """
    with session(db) as s:
        query = select(Decision.run_id).where(Decision.card_id == card_id)
        run_id = s.scalar(query.order_by(Decision.decided_at.desc()).limit(1))
        if run_id is None:
            return []
        rows = s.scalars(
            select(Decision)
            .where(Decision.run_id == run_id, Decision.card_id == card_id)
            .order_by(Decision.ts_sim, Decision.decided_at)
        )
        return [entry_of(r) for r in rows]


def policy_lineage(db: Engine, mandate_id: str) -> set[str]:
    """This mandate's id and those of the mandates holding the same policy: a D3 move
    (``move_mandate``) copies a policy under a new id with the same Viseca mandate, and a
    run already deciding under the original keeps its id on the decisions it stored."""
    with session(db) as s:
        viseca_id = s.scalar(select(Mandate.viseca_mandate_id).where(Mandate.mandate_id == mandate_id))
        if viseca_id is None:
            return {mandate_id}
        return {mandate_id, *s.scalars(select(Mandate.mandate_id).where(Mandate.viseca_mandate_id == viseca_id))}


def requested_item_marks(db: Engine, live_ids: Iterable[str]) -> dict[str, str]:
    """Live id -> requested item, for the ids among ``live_ids`` whose cart held their
    single-item mandate's requested item (``requested_item_orders``, A8)."""
    ids = list(live_ids)
    if not ids:
        return {}
    with session(db) as s:
        rows = s.scalars(select(RequestedItemOrder).where(RequestedItemOrder.live_authorization_id.in_(ids)))
        return {row.live_authorization_id: row.item for row in rows}


# Drafts and mandates --------------------------------------------------------------------


def add(db: Engine, row: Any) -> None:
    with session(db) as s:
        s.add(row)


def draft(db: Engine, draft_id: str) -> PolicyDraft | None:
    with session(db) as s:
        return s.get(PolicyDraft, draft_id)


def latest_mandate(db: Engine, card_id: str) -> Mandate | None:
    """The card's current mandate: the active one, else the one confirmed last."""
    with session(db) as s:
        rows = list(
            s.scalars(
                select(Mandate)
                .where(Mandate.card_id == card_id)
                .order_by(Mandate.confirmed_at.desc(), Mandate.mandate_id.desc())
            )
        )
    active = [m for m in rows if m.status == "active"]
    return (active or rows or [None])[0]


def mandates_by_id(db: Engine, mandate_ids: Iterable[str]) -> dict[str, Mandate]:
    ids = sorted(set(mandate_ids))
    if not ids:
        return {}
    with session(db) as s:
        return {m.mandate_id: m for m in s.scalars(select(Mandate).where(Mandate.mandate_id.in_(ids)))}


def mandates(db: Engine, status: str | None = None) -> list[Mandate]:
    with session(db) as s:
        query = select(Mandate).order_by(Mandate.confirmed_at)
        if status is not None:
            query = query.where(Mandate.status == status)
        return list(s.scalars(query))


def latest_mandates_by_card(db: Engine, card_ids: Iterable[str]) -> dict[str, Mandate]:
    """card id → the card's active mandate, for cards that have one."""
    ids = sorted(set(card_ids))
    if not ids:
        return {}
    with session(db) as s:
        rows = s.scalars(
            select(Mandate)
            .where(Mandate.card_id.in_(ids), Mandate.status == "active")
            .order_by(Mandate.confirmed_at)
        )
        return {m.card_id: m for m in rows}


def confirm_draft(
    db: Engine,
    draft_id: str,
    mandate: Mandate,
    viseca_draft_id: str | None,
    at: datetime,
    ended: str = "revoked",
    note: str | None = None,
) -> list[Mandate]:
    """Store ``mandate``, mark the draft confirmed, and end the card's earlier active
    mandates (a new policy replaces the old one): ``revoked``, or ``superseded`` with
    ``note`` when an operator's run replaced it (D3). Returns the mandates it ended."""
    with session(db) as s:
        replaced = list(
            s.scalars(
                select(Mandate).where(Mandate.card_id == mandate.card_id, Mandate.status == "active")
            )
        )
        for old in replaced:
            old.status = ended
            old.revoked_at = at
            old.note = note
        row = s.get(PolicyDraft, draft_id)
        assert row is not None
        row.confirmed_at = at
        row.viseca_draft_id = viseca_draft_id
        s.add(mandate)
    return replaced


def move_mandate(
    db: Engine, mandate_id: str, card_id: str, customer_id: str, at: datetime, new_id: str
) -> tuple[Mandate, list[Mandate]]:
    """D3: the platform ran a mandate's scenario on another card than the one it was
    confirmed on. The policy moves to that card: a copy (same instruction, rules and
    Viseca mandate) becomes the card's active mandate and the original is marked revoked
    here only, since the same Viseca mandate stays in force. Returns the copy and the
    card's earlier active mandates, revoked, which the caller revokes at the platform."""
    with session(db) as s:
        source = s.get(Mandate, mandate_id)
        assert source is not None
        replaced = list(s.scalars(select(Mandate).where(Mandate.card_id == card_id, Mandate.status == "active")))
        for old in replaced:
            old.status = "revoked"
            old.revoked_at = at
        source.status = "revoked"
        source.revoked_at = at
        moved = Mandate(
            mandate_id=new_id,
            viseca_mandate_id=source.viseca_mandate_id,
            card_id=card_id,
            customer_id=customer_id,
            instruction=source.instruction,
            rules=source.rules,
            checks=source.checks,
            uncertainty_policy=source.uncertainty_policy,
            open_questions=source.open_questions,
            status="active",
            confirmed_at=source.confirmed_at,
            revoked_at=None,
            note=source.note,
        )
        s.add(moved)
        s.flush()
        return moved, replaced


def update_mandate(db: Engine, mandate_id: str, **fields: Any) -> Mandate:
    with session(db) as s:
        row = s.get(Mandate, mandate_id)
        assert row is not None
        for key, value in fields.items():
            setattr(row, key, value)
        s.flush()
        return row


def replay_run(db: Engine, run: Run) -> None:
    with session(db) as s:
        s.merge(run)


def newest_run(db: Engine) -> Run | None:
    """The run started last on the real clock, live or replay."""
    with session(db) as s:
        return s.scalar(select(Run).order_by(Run.started_at.desc(), Run.run_id.desc()).limit(1))


def run_row(db: Engine, run_id: str) -> Run | None:
    with session(db) as s:
        return s.get(Run, run_id)


def _recorded(scenario_id: str | None = None) -> Any:
    """Live runs with stored events (``events_raw``), of one scenario when given."""
    query = select(Run).where(Run.kind == "live", Run.run_id.in_(select(EventRaw.run_id).distinct()))
    return query if scenario_id is None else query.where(Run.scenario_id == scenario_id)


def record_run(db: Engine, scenario_id: str) -> Run | None:
    """The scenario's newest live run with stored events: what D2 replays from record."""
    with session(db) as s:
        return s.scalar(_recorded(scenario_id).order_by(Run.started_at.desc(), Run.run_id.desc()).limit(1))


def recorded_scenarios(db: Engine) -> set[str]:
    """The scenarios with a live run whose events are stored (D9 ``replay_source``)."""
    with session(db) as s:
        return {sid for sid in s.scalars(_recorded().with_only_columns(Run.scenario_id).distinct()) if sid}


def stored_events(db: Engine, run_id: str) -> list[dict[str, Any]]:
    """The run's events as received, in the order they arrived."""
    with session(db) as s:
        rows = s.scalars(
            select(EventRaw.event)
            .where(EventRaw.run_id == run_id)
            .order_by(EventRaw.received_at, EventRaw.live_authorization_id)
        )
        return [dict(event) for event in rows]


def unfinished_live_runs(db: Engine) -> list[Run]:
    """Live runs the store last saw starting or running."""
    with session(db) as s:
        return list(
            s.scalars(
                select(Run)
                .where(Run.kind == "live", Run.state.in_(("starting", "running")))
                .order_by(Run.started_at.desc())
            )
        )


def live_run_row(db: Engine, viseca_run_id: str) -> Run | None:
    with session(db) as s:
        return s.scalar(select(Run).where(Run.viseca_run_id == viseca_run_id, Run.kind == "live"))


def round_trip_ms(db: Engine) -> float:
    """One ``SELECT 1`` through a pooled connection, in milliseconds."""
    import time

    with db.connect() as conn:
        started = time.perf_counter()
        conn.execute(text("SELECT 1")).scalar_one()
        return round((time.perf_counter() - started) * 1000, 1)


def card_draft_rules(db: Engine, card_id: str) -> dict[str, dict[str, Any]]:
    """check id → typed rule (stored form) from this card's drafts, the newest draft first."""
    from oneguard.api.policies import POLICY_KEY

    found: dict[str, dict[str, Any]] = {}
    with session(db) as s:
        rows = s.scalars(
            select(PolicyDraft).where(PolicyDraft.card_id == card_id).order_by(PolicyDraft.created_at.desc())
        )
        for row in rows:
            for check_id, rule in (row.rules or {}).items():
                if check_id != POLICY_KEY:
                    found.setdefault(check_id, rule)
    return found


def restore_form_instructions(db: Engine, words: str) -> int:
    """Form drafts, and the mandates confirmed from them, stored before the form path kept
    ``words`` held the joined check texts as their instruction; set them to ``words``.

    A mandate is matched to its form draft by card and confirmation time (C2 writes both in
    one transaction under the policy lock). Idempotent; returns the mandates changed.
    """
    from_form = (
        select(PolicyDraft.draft_id)
        .where(
            PolicyDraft.compiler == "form",
            PolicyDraft.card_id == Mandate.card_id,
            PolicyDraft.confirmed_at == Mandate.confirmed_at,
        )
        .exists()
    )
    with session(db) as s:
        mandates = s.execute(
            update(Mandate).where(Mandate.instruction != words, from_form).values(instruction=words)
        ).rowcount
        s.execute(
            update(PolicyDraft)
            .where(PolicyDraft.compiler == "form", PolicyDraft.instruction != words)
            .values(instruction=words)
        )
    return mandates
