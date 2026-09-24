"""SQLAlchemy models for every table in docs/database.md §2 (frozen interface file).

Conventions (docs/database.md §1): JSON columns are ``jsonb`` on Postgres, timestamps
are ``DateTime(timezone=True)`` stored and returned in UTC (also on SQLite, which keeps
no offset), money is ``Numeric(12, 2)``. Reference tables are seeded from ``data/``
by ``store/seed.py`` and read-only at runtime; ``decisions`` is written only by P2's
``engine/ledger.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that only accepts aware values and returns UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; store timezone-aware UTC values only")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


Json = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(12, 2)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UtcDateTime(),
        date: Date(),
        Decimal: Money,
        str: String(),
        dict[str, Any]: Json,
        list[Any]: Json,
    }


# Reference data -----------------------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(primary_key=True)
    persona_name: Mapped[str]
    home_region: Mapped[str]
    background: Mapped[str] = mapped_column(Text)
    shopping_preferences: Mapped[str] = mapped_column(Text)
    typical_spending: Mapped[str] = mapped_column(Text)
    budget_style: Mapped[str]
    travel_pattern: Mapped[str] = mapped_column(Text)


class Account(Base):
    """Bank limits are context only, never on the meter."""

    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    account_type: Mapped[str]
    account_purpose: Mapped[str]
    base_currency: Mapped[str]
    status: Mapped[str]
    opened_on: Mapped[date]
    per_transaction_limit_chf: Mapped[Decimal]
    monthly_limit_chf: Mapped[Decimal]


class Card(Base):
    __tablename__ = "cards"

    card_id: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    card_type: Mapped[str]
    card_purpose: Mapped[str]
    status: Mapped[str]
    first_used_on: Mapped[date]
    expires_on: Mapped[date]
    online_enabled: Mapped[bool] = mapped_column(Boolean)
    international_enabled: Mapped[bool] = mapped_column(Boolean)
    virtual_card: Mapped[bool] = mapped_column(Boolean)


class Merchant(Base):
    """``name_normalised`` is ``history.normalise_merchant_name(merchant_name)`` (A7)."""

    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(primary_key=True)
    merchant_name: Mapped[str]
    name_normalised: Mapped[str] = mapped_column(index=True)
    merchant_category: Mapped[str]
    merchant_mcc: Mapped[str] = mapped_column(String(4))
    merchant_country: Mapped[str] = mapped_column(String(2))
    merchant_city: Mapped[str]
    availability: Mapped[str]
    recurring_capable: Mapped[bool] = mapped_column(Boolean)


class Item(Base):
    __tablename__ = "items"

    item_id: Mapped[str] = mapped_column(primary_key=True)
    item_name: Mapped[str]
    item_category: Mapped[str]
    item_description: Mapped[str] = mapped_column(Text)
    unit_price_min_chf: Mapped[Decimal]
    unit_price_typical_chf: Mapped[Decimal]
    unit_price_max_chf: Mapped[Decimal]


class FxRate(Base):
    """Rates keep six decimals; they are not money."""

    __tablename__ = "fx_rates"

    from_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    to_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    rate_date: Mapped[date]
    source: Mapped[str]


class AuthorizationHistory(Base):
    """One historical authorization (4,701 rows). Refunds are negative amounts."""

    __tablename__ = "authorization_history"
    __table_args__ = (
        Index("ix_history_card_ts", "card_id", "timestamp"),
        Index("ix_history_customer_merchant", "customer_id", "merchant_id"),
        Index("ix_history_customer_device", "customer_id", "customer_device_id"),
        Index("ix_history_customer_country", "customer_id", "merchant_country"),
    )

    authorization_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.card_id"))
    initiator_type: Mapped[str]
    timestamp: Mapped[datetime]
    transaction_type: Mapped[str]
    status: Mapped[str]
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3))
    billing_amount_chf: Mapped[Decimal]
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"))
    merchant_name: Mapped[str]
    merchant_category: Mapped[str]
    merchant_mcc: Mapped[str] = mapped_column(String(4))
    merchant_country: Mapped[str] = mapped_column(String(2))
    merchant_city: Mapped[str]
    channel: Mapped[str]
    card_present: Mapped[bool] = mapped_column(Boolean)
    recurring: Mapped[bool] = mapped_column(Boolean)
    customer_device_id: Mapped[str | None]
    description: Mapped[str]
    related_transaction_id: Mapped[str | None]
    account_type: Mapped[str]
    account_purpose: Mapped[str]
    base_currency: Mapped[str] = mapped_column(String(3))
    per_transaction_limit_chf: Mapped[Decimal]
    monthly_limit_chf: Mapped[Decimal]
    card_purpose: Mapped[str]
    card_status: Mapped[str]
    online_enabled: Mapped[bool] = mapped_column(Boolean)
    international_enabled: Mapped[bool] = mapped_column(Boolean)
    virtual_card: Mapped[bool] = mapped_column(Boolean)
    customer_home_region: Mapped[str]
    customer_budget_style: Mapped[str]
    customer_persona_name: Mapped[str]
    approved_spend_before_chf: Mapped[Decimal]
    approved_merchant_transaction_count_before: Mapped[int] = mapped_column(Integer)
    approved_device_transaction_count_before: Mapped[int] = mapped_column(Integer)
    last_approved_at: Mapped[datetime | None]


class ScenarioCatalogue(Base):
    """Used only by ``api/routes_dev.py`` and ``replay/``."""

    __tablename__ = "scenario_catalogue"

    scenario_id: Mapped[str] = mapped_column(primary_key=True)
    scenario_name: Mapped[str]
    cardholder_instruction: Mapped[str] = mapped_column(Text)
    control_question: Mapped[str] = mapped_column(Text)
    control_theme: Mapped[str]
    event_count: Mapped[int] = mapped_column(Integer)
    short_rationale: Mapped[str] = mapped_column(Text)


class ScenarioAuthority(Base):
    """Used only by ``api/routes_dev.py`` and ``replay/``."""

    __tablename__ = "scenario_authorities"

    authority_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.card_id"))
    valid_from: Mapped[datetime]
    valid_until: Mapped[datetime]
    initial_status: Mapped[str]


REFERENCE_TABLES: tuple[type[Base], ...] = (
    Customer,
    Account,
    Card,
    Merchant,
    Item,
    FxRate,
    AuthorizationHistory,
    ScenarioCatalogue,
    ScenarioAuthority,
)
"""Seeded from ``data/<table>.csv``, in foreign-key order."""


# Runtime tables -------------------------------------------------------------------------


class PolicyDraft(Base):
    """C1. ``rules`` holds the typed rules keyed by RuleCheck id."""

    __tablename__ = "policy_drafts"

    draft_id: Mapped[str] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(index=True)
    customer_id: Mapped[str]
    instruction: Mapped[str] = mapped_column(Text)
    rules: Mapped[dict[str, Any]]
    checks: Mapped[list[Any]]
    uncertainty_policy: Mapped[str]
    open_questions: Mapped[list[Any]]
    dry_run: Mapped[dict[str, Any]]
    compiler: Mapped[str]
    viseca_draft_id: Mapped[str | None]
    created_at: Mapped[datetime]
    confirmed_at: Mapped[datetime | None]


class Mandate(Base):
    """C2, C4, C5. Revoked mandates are kept, never deleted."""

    __tablename__ = "mandates"
    __table_args__ = (Index("ix_mandates_card_status", "card_id", "status"),)

    mandate_id: Mapped[str] = mapped_column(primary_key=True)
    viseca_mandate_id: Mapped[str | None] = mapped_column(index=True)
    card_id: Mapped[str]
    customer_id: Mapped[str]
    instruction: Mapped[str] = mapped_column(Text)
    rules: Mapped[dict[str, Any]]
    checks: Mapped[list[Any]]
    uncertainty_policy: Mapped[str]
    open_questions: Mapped[list[Any]]
    status: Mapped[str]
    confirmed_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class Run(Base):
    """A live or replay run. ``scenario_id`` is operator metadata only."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(primary_key=True)
    viseca_run_id: Mapped[str | None] = mapped_column(index=True)
    kind: Mapped[str]
    scenario_id: Mapped[str | None]
    mandate_id: Mapped[str]
    card_id: Mapped[str]
    state: Mapped[str]
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    decided: Mapped[int] = mapped_column(Integer, default=0)
    pending_human: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    worker_last_poll_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)


class EventRaw(Base):
    """The full validated event: what makes any decision reproducible."""

    __tablename__ = "events_raw"

    live_authorization_id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(index=True)
    source_authorization_id: Mapped[str]
    received_at: Mapped[datetime]
    deadline_at: Mapped[datetime]
    event: Mapped[dict[str, Any]]


class Decision(Base):
    """The ledger (engine/ledger_base.LedgerEntry). Written by P2's ledger.py only.

    ``step`` / ``deciding_ids`` rebuild the EngineDecision on redelivery;
    ``deadline_at`` is the real-clock end of a pending step-up's human window.
    """

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_run_ts", "run_id", "ts_sim"),
        Index("ix_decisions_card_ts", "card_id", "ts_sim"),
        Index("ix_decisions_customer_ts", "customer_id", "ts_sim"),
    )

    live_authorization_id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str]
    mandate_id: Mapped[str]
    card_id: Mapped[str]
    customer_id: Mapped[str]
    ts_sim: Mapped[datetime]
    outcome: Mapped[str]
    final: Mapped[bool] = mapped_column(Boolean)
    uncertain_outcome: Mapped[str | None]
    reserved_chf: Mapped[Decimal]
    spent_chf: Mapped[Decimal]
    merchant_id: Mapped[str]
    item_ids: Mapped[list[Any]]
    billing_amount_chf: Mapped[Decimal]
    related_live_id: Mapped[str | None]
    relation: Mapped[str | None]
    session_trust: Mapped[str]
    step: Mapped[int] = mapped_column(Integer)
    deciding_ids: Mapped[list[Any]]
    reason_codes: Mapped[list[Any]]
    evidence: Mapped[list[Any]]
    message: Mapped[str] = mapped_column(Text)
    counterfactual: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str]
    injection_flag: Mapped[dict[str, Any] | None]
    engine_version: Mapped[str]
    latency_ms: Mapped[float]
    signals_enabled: Mapped[bool] = mapped_column(Boolean)
    decided_at: Mapped[datetime]
    deadline_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None]


class MerchantFlag(Base):
    """A1 info evidence for later purchases at a shop in the same run."""

    __tablename__ = "merchant_flags"
    __table_args__ = (Index("ix_merchant_flags_run_merchant", "run_id", "merchant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str]
    merchant_id: Mapped[str]
    flagged_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(Text)


class WorkerState(Base):
    """The worker's durable position in Viseca's feeds, one row per ``key``.

    ``events_cursor``: the ``next_cursor`` of the last ``GET /v1/events`` page the worker
    processed, JSON so an int or a string cursor comes back as it was served.
    """

    __tablename__ = "worker_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any] = mapped_column(Json)
    updated_at: Mapped[datetime]


class ScenarioProfile(Base):
    """Which customer and card a served scenario runs on. Used only by ``api/routes_dev.py``
    (and C12 through it) and the worker that writes it.

    The served catalogue names no card; the platform says it in the bootstrap ``profile``
    (one scenario) and in every run's ``fixture_profiles`` and authorizations, so a row is
    written as soon as any of those names the scenario, the newest sighting winning.
    ``source``: ``bootstrap`` | ``run`` | ``authorization``.
    """

    __tablename__ = "scenario_profiles"

    scenario_id: Mapped[str] = mapped_column(primary_key=True)
    profile_id: Mapped[str | None]
    customer_id: Mapped[str]
    card_id: Mapped[str]
    source: Mapped[str]
    seen_at: Mapped[datetime]


class VisecaCall(Base):
    """Append-only log of Viseca requests: summaries only, never the key, ≤ 4 KB each."""

    __tablename__ = "viseca_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    called_at: Mapped[datetime] = mapped_column(index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str]
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float]
    request_summary: Mapped[str | None] = mapped_column(Text)
    response_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
