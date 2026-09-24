"""Shapes passed between lanes (frozen interface file, docs/team-contract.md §3).

Every model is Pydantic v2 with ``extra="forbid"``. Rule ids in docstrings refer to
docs/rules.md. Only P1 changes this file; lanes request a field with a ``contract:`` PR.

Money is a float in CHF unless the field says otherwise; lanes round half-even to 2 dp
with ``decimal`` before comparing (rules M1, M2). Timestamps are timezone-aware; the
simulated purchase time is ``Facts.timestamp`` (M6).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from oneguard.api.models import DryRunResult

Outcome = Literal["approve", "decline", "step_up"]
RuleOutcome = Literal["pass", "fail", "unknown"]
FactSource = Literal["event", "regex", "model", "history"]
EvidenceOutcome = Literal["pass", "fail", "uncertain", "info"]
EvidenceSource = Literal["policy", "ledger", "history", "merchant_text", "model"]
Currency = Literal["CHF", "EUR", "GBP", "USD"]
OrderTerm = Literal["true", "false", "unknown", "not_applicable"]
UncertaintyPolicy = Literal["ask", "decline", "approve"]
SessionTrust = Literal["normal", "elevated", "frozen"]
Relation = Literal["requote_of", "duplicate_of", "retry_of", "split_of"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
RuleOperator = Literal["<", "<=", "=", "!=", ">", ">=", "in", "not_in"]
RuleKind = Literal["amount", "period", "merchant", "item", "terms", "session", "other"]
SignalId = Literal[
    "A1", "A2", "A3", "A4", "A5", "A6", "A7",
    "W1", "W2", "W3", "W4", "W5", "W6",
    "S_agent_directed",
]

# rules.md §4 step 1 has no customer rule behind it. evaluate_rules reports it as
# RuleResults with these ids (fail = the field named in `detail` is not active), and
# decide treats a fail on any of them as step 1, before every other rule.
STEP1_RULE_IDS: tuple[str, ...] = ("policy_status", "authority_status", "card_status")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactValue[T](_Model):
    """One fact the rules need that may be missing (P3, rule 4 of CLAUDE.md).

    Serves C6, C7, A2, A6: ``source="regex"`` for allowlisted extraction from shop
    text, ``"model"`` for tier-2 extraction (§4a), ``"event"`` / ``"history"`` for
    trusted fields. ``known=False`` means unknown (absent or self-contradictory, the
    reason in ``detail``); it is never a pass. A known fact always has a value.
    """

    value: T | None = None
    known: bool
    source: FactSource
    detail: str = ""

    @model_validator(mode="after")
    def _known_has_value(self) -> FactValue[T]:
        if self.known and self.value is None:
            raise ValueError("a known FactValue must carry a value")
        return self


class ItemFacts(_Model):
    """One cart line (C3, C4, C5, C6, C10, A3, A6, W6; M1, M2).

    ``item_name`` and ``item_details`` are untrusted shop text: facts come from them
    only through the FactValues. ``size_eu`` is decimal (43.5 is a real size and is not
    43); ``size_letter`` is XS-XXXL for clothing (C6). The catalogue range comes from ``items`` and is
    ``None`` when the ``item_id`` is not in the catalogue.
    """

    line_no: int = Field(ge=1)
    item_id: str
    item_name: str
    item_category: str
    quantity: int = Field(ge=1)
    unit_price: float
    currency: Currency
    unit_price_chf: float
    item_details: str
    size_eu: FactValue[float]
    size_letter: FactValue[str] = Field(
        default_factory=lambda: FactValue[str](known=False, source="regex", detail="not extracted")
    )
    return_window_days: FactValue[int]
    recurring: FactValue[bool]
    unit_price_min_chf: float | None = None
    unit_price_typical_chf: float | None = None
    unit_price_max_chf: float | None = None


class Facts(_Model):
    """Everything the gate may use about one purchase (§4 step 1, C1–C12, A1–A7, W1–W6).

    Built by ``build_facts`` from the event's trusted fields plus regex extraction.
    ``timestamp`` is simulated time (M6); ``local_weekday`` / ``local_hour`` are in
    Europe/Zurich (W5, C12). ``billing_amount_chf`` already includes delivery (M3).
    ``merchant_known`` (customer level, Q7) and ``merchant_known_on_card`` (C9) are
    set by the pipeline from the LedgerView after ``build_facts``.
    ``order_returnable`` / ``order_cancellable`` keep the four strings distinct
    (``unknown`` ≠ ``not_applicable``); ``delivery_by`` ``None`` means not supplied.
    ``agent_directed_text`` is filled by signals (A1, S_agent_directed), empty by
    default. ``merchant_name`` is untrusted.
    """

    authorization_id: str
    source_authorization_id: str
    timestamp: AwareDatetime
    local_weekday: Weekday
    local_hour: int = Field(ge=0, le=23)
    amount: float
    currency: Currency
    billing_amount_chf: float
    items: list[ItemFacts] = Field(min_length=1)
    merchant_id: str
    merchant_name: str
    merchant_category: str
    merchant_country: str
    merchant_mcc: str
    merchant_recurring_capable: bool
    merchant_known: bool = False
    merchant_known_on_card: bool = False
    device_id: str
    recent_attempt_count_10m: int = Field(ge=0)
    order_returnable: OrderTerm
    order_cancellable: OrderTerm
    delivery_by: date | None
    related_authorization_id: str | None
    related_status: Literal["pending", "approved", "declined", "cancelled"] | None
    return_window_days: FactValue[int]
    authority_status: Literal["active", "revoked", "expired"]
    card_status_at_attempt: Literal["active", "blocked"]
    agent_directed_text: list[str] = Field(default_factory=list)


class Rule(_Model):
    """One typed customer rule behind a RuleCheck (C1–C12, §10 T1–T4).

    ``field`` / ``operator`` / ``value`` / ``currency`` / ``scope`` / ``period_days``
    use Viseca's rule format and the vocabulary of docs/api-contract.md §3.3. ``on_fail``
    is ``ask`` when the customer asked to be asked rather than declined ("same price as
    last time, ask me if anything changed"): a broken rule is then unknown (C11). ``id``,
    ``text``, ``source``, ``kind`` and ``uncertainty`` are what the UI sees as a
    RuleCheck. ``value`` is never a boolean, null, object or list of numbers.
    """

    id: str
    field: str
    operator: RuleOperator
    value: StrictInt | StrictFloat | StrictStr | list[StrictStr]
    currency: Currency | None = None
    scope: Literal["purchase", "period"] | None = None
    period_days: int | None = Field(default=None, ge=1)
    text: str
    source: Literal["exact", "inferred"]
    kind: RuleKind | None = None
    uncertainty: str | None = None
    on_fail: Literal["decline", "ask"] = "decline"


class Policy(_Model):
    """The confirmed mandate the gate applies (§3 Policy, C1–C12, T5–T6, Q6).

    ``status`` feeds §4 step 1 (revoked / expired policy). ``uncertainty_policy`` is
    C11. The convenience fields restate rules already in ``rules``: ``None`` or
    ``False`` means the customer did not state that rule, never "anything goes"
    (C3 ``allowed_item_categories``, C4 ``blocked_item_categories``, C5
    ``requested_item``, C8 ``shop_type``, C9 ``requires_known_shop``, C10
    ``nothing_extra``).
    """

    mandate_id: str
    status: Literal["active", "superseded", "revoked", "expired"]
    instruction: str
    rules: list[Rule]
    uncertainty_policy: UncertaintyPolicy
    requested_item: str | None = None
    allowed_item_categories: list[str] | None = None
    blocked_item_categories: list[str] | None = None
    requires_known_shop: bool = False
    nothing_extra: bool = False
    shop_type: str | None = None


class PriorDecision(_Model):
    """An earlier decision in this run, as the ledger remembers it (A3, A4, A5, M4, M5).

    ``final`` is True for approvals and declines, and for step-ups once answered.
    ``reserved`` is True while a step-up is pending (M5).
    """

    authorization_id: str
    timestamp: AwareDatetime
    outcome: Outcome
    final: bool
    merchant_id: str
    item_ids: list[str]
    billing_amount_chf: float
    reserved: bool


class LedgerView(_Model):
    """The ledger's state as of one purchase (C2, C9, M4, M5, M7, A1, A3, A4, W1, W3, W4).

    Built by ``Ledger.view`` from the ``decisions`` table (this run) and HistoryIndex
    (history). ``period_spent_chf`` counts final approvals only; pending step-ups are
    in ``period_reserved_chf``. Known sets are history ∪ this run's final approvals:
    ``known_merchant_ids`` is customer level (Q7), ``known_merchant_ids_on_card`` card
    level. ``merchant_approvals_on_card`` / ``merchant_approvals_other_cards`` count
    approved purchases per ``merchant_id`` for ``Decision.merchant_meta``.
    ``max_approved_chf`` is ``None`` when the customer has no approved purchase (W4).
    ``flagged_merchant_ids`` carries A1 info evidence to later purchases.
    """

    period_spent_chf: float
    period_reserved_chf: float
    period_window_start: AwareDatetime
    priors: list[PriorDecision]
    known_merchant_ids: set[str]
    known_merchant_ids_on_card: set[str]
    merchant_approvals_on_card: dict[str, int] = Field(default_factory=dict)
    merchant_approvals_other_cards: dict[str, int] = Field(default_factory=dict)
    known_device_ids: set[str]
    known_countries: set[str]
    max_approved_chf: float | None
    flagged_merchant_ids: set[str]
    frozen: bool


class RuleResult(_Model):
    """The outcome of one customer rule, or a step-1 check (C1–C12, §4 steps 1, 2, 4).

    ``unknown`` is never a pass (P3). ``counterfactual`` says what would make a fail a
    pass (E3). ``rule_id`` is a ``Rule.id`` or one of ``STEP1_RULE_IDS``.
    """

    rule_id: str
    outcome: RuleOutcome
    detail: str
    counterfactual: str | None = None
    source: FactSource


class Signal(_Model):
    """One protection or warning sign (A1–A7, W1–W6) or the agent_directed soft signal.

    ``strength`` is ``protection`` for A-ids, ``strong`` / ``weak`` for W-ids (§8
    W-rules). ``outcome_if_triggered`` is what the signal asks of decide. ``related``
    is ``(authorization_id, relation)`` for A3/A4/A5. Soft signals only ever add
    friction (P5).
    """

    id: SignalId
    triggered: bool
    strength: Literal["strong", "weak", "protection"]
    outcome_if_triggered: Literal["ask", "decline", "info"]
    detail: str
    source: EvidenceSource
    related: tuple[str, Relation] | None = None


class EngineDecision(_Model):
    """The gate's result (§4 steps 1–7, P4, D1–D3, M5).

    ``step`` is the §4 step that decided. ``deciding_ids`` are the rule and signal
    ids behind it. ``reason_codes`` use the vocabulary of docs/api-contract.md §4.
    """

    outcome: Outcome
    reason_codes: list[str]
    step: int = Field(ge=1, le=7)
    deciding_ids: list[str]
    related: tuple[str, Relation] | None = None
    session_trust: SessionTrust = "normal"


class EvidenceRow(_Model):
    """One fact the decision used (E7); the API's ``Evidence`` shape."""

    rule: str
    outcome: EvidenceOutcome
    detail: str
    source: EvidenceSource | None = None


class Explanation(_Model):
    """What the customer reads (E1–E8, P7).

    ``message`` is one sentence naming the rule and the number (E1, E2);
    ``counterfactual`` is E3; ``injection_flag`` is ``{"flagged": True, "reason": …}``
    for A1 (E5) or ``None``. ``source`` is ``model`` only after a tier-3 rewrite.
    """

    message: str
    counterfactual: str | None = None
    evidence: list[EvidenceRow]
    injection_flag: dict[str, bool | str] | None = None
    source: Literal["template", "model"] = "template"


class CompiledDraft(_Model):
    """The compiler's output for C1 (§10 T1–T7, docs/api-contract.md §3.2).

    ``rules`` become the draft's RuleChecks; the Policy fields restate them as in
    ``Policy``. ``compiler`` is ``fallback`` when the LLM was unavailable.
    """

    instruction: str
    rules: list[Rule]
    uncertainty_policy: Literal["ask", "decline"]
    open_questions: list[str]
    dry_run: DryRunResult
    compiler: Literal["llm", "form", "fallback"]
    requested_item: str | None = None
    allowed_item_categories: list[str] | None = None
    blocked_item_categories: list[str] | None = None
    requires_known_shop: bool = False
    nothing_extra: bool = False
    shop_type: str | None = None


class HistoryRow(_Model):
    """One ``authorization_history`` row as ``HistoryIndex.recent_rows`` returns it (T2 dry-run).

    All transaction types and both statuses; the caller filters. Refunds are negative.
    """

    authorization_id: str
    customer_id: str
    card_id: str
    initiator_type: Literal["human", "agent", "merchant"]
    timestamp: AwareDatetime
    transaction_type: Literal["purchase", "refund", "cash_withdrawal"]
    status: Literal["approved", "declined"]
    amount: float
    currency: Currency
    billing_amount_chf: float
    merchant_id: str
    merchant_name: str
    merchant_category: str
    merchant_country: str
    channel: str
    recurring: bool
    customer_device_id: str | None
    description: str


@runtime_checkable
class HistoryIndex(Protocol):
    """Read-only view of ``authorization_history`` and the catalogue (docs/database.md §3).

    Familiarity (C9, Q7, W1, W3, W4, A7) counts approved purchases only; refunds and
    cash withdrawals never make a shop, device or country familiar.
    """

    def known_merchants(self, customer_id: str) -> Mapping[str, int]:
        """merchant_id → approved purchases by this customer on any card (C9, Q7)."""
        ...

    def known_merchants_on_card(self, card_id: str) -> Mapping[str, int]:
        """merchant_id → approved purchases on this card (card-level C9)."""
        ...

    def known_devices(self, customer_id: str) -> frozenset[str]:
        """Device ids used for an approved purchase by this customer (W1)."""
        ...

    def known_countries(self, customer_id: str) -> frozenset[str]:
        """Merchant countries of this customer's approved purchases (W3)."""
        ...

    def max_approved(self, customer_id: str) -> float | None:
        """Largest approved purchase in CHF, ``None`` with no approved purchase (W4)."""
        ...

    def last_price(self, customer_id: str, merchant_id: str) -> float | None:
        """CHF total of the latest approved purchase at this shop, or ``None``."""
        ...

    def recent_rows(
        self, card_id: str, days: int, as_of: datetime | None = None
    ) -> list[HistoryRow]:
        """Rows on this card in the ``days`` before ``as_of`` (default: end of history)."""
        ...

    def merchant_names_normalised(self) -> Mapping[str, str]:
        """merchant_id → normalised merchant name for every catalogue shop (A7)."""
        ...

    def agent_history(self, customer_id: str) -> tuple[int, int]:
        """(attempts, approved) over history rows with ``initiator_type`` agent."""
        ...

    def item_price_range(self, item_id: str) -> tuple[float, float, float] | None:
        """(min, typical, max) CHF unit price from ``items``, ``None`` if unknown (W6)."""
        ...
