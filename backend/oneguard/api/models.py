"""Wire shapes of docs/api-contract.md (frozen interface file, docs/team-contract.md §3).

One model per type in §2, the envelopes of §1.0, the request bodies of §1.1 / §1.2 and
the error body of §3.8. ``extra="forbid"`` everywhere. If this file and the contract
disagree, this file wins and the contract is fixed in the same commit.

Two kinds of optional field, kept apart because the UI's types.ts keeps them apart:
- nullable (``x: T | null``, ``x?: T | null``): serialised as an explicit ``null``;
- optional-not-nullable (``x?: T``): omitted from the JSON when ``None``. Each model
  lists these in ``_omit_if_none``.

Timestamps serialise as ISO 8601 UTC with ``Z`` and second precision (Appendix A).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_serializer,
    model_validator,
)


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


Timestamp = Annotated[AwareDatetime, PlainSerializer(_utc_z, return_type=str)]
"""A timezone-aware instant, serialised as ``YYYY-MM-DDTHH:MM:SSZ``."""

UncertaintyChoice = Literal["ask", "decline"]
ReturnTerm = Literal["true", "false", "unknown", "not_applicable"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _omit_if_none: ClassVar[frozenset[str]] = frozenset()

    @model_serializer(mode="wrap")
    def _drop_absent_optionals(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            for key in self._omit_if_none:
                if data.get(key, ...) is None:
                    del data[key]
        return data


# §2 types ----------------------------------------------------------------------------


class Customer(ApiModel):
    customer_id: str
    name: str
    home_region: str
    card_id: str | None
    scenario_ids: list[str]
    live: bool


class Card(ApiModel):
    card_id: str
    card_type: str
    card_purpose: str
    status: str


class Account(ApiModel):
    """Bank limits are context only; they never appear on the LeashMeter."""

    account_id: str
    customer_id: str
    account_type: str
    account_purpose: str
    status: str
    per_transaction_limit_chf: float
    monthly_limit_chf: float
    cards: list[Card]


class RuleCheck(ApiModel):
    _omit_if_none = frozenset({"kind"})

    id: str
    text: str
    source: Literal["exact", "inferred"]
    uncertainty: str | None
    kind: Literal["amount", "period", "merchant", "item", "terms", "session", "other"] | None = None


class FormInput(ApiModel):
    per_order_limit_chf: float | None
    period_limit_chf: float | None
    period_days: Literal[7, 14, 30] | None
    categories: list[str]
    sellers_used_before_only: bool
    uncertainty_policy: UncertaintyChoice


class DryRunExample(ApiModel):
    occurred_at: Timestamp
    merchant_name: str
    billing_amount_chf: float
    outcome: Literal["fit", "violate", "ask"]
    reason: str


class AgentHistory(ApiModel):
    attempts: int = Field(ge=0)
    approved: int = Field(ge=0)


class DryRunResult(ApiModel):
    _omit_if_none = frozenset({"examples", "agent_history"})

    sample_size: int = Field(ge=0)
    would_violate: int = Field(ge=0)
    would_fit: int = Field(ge=0)
    would_ask: int = Field(ge=0)
    insight: str
    examples: list[DryRunExample] | None = Field(default=None, max_length=3)
    agent_history: AgentHistory | None = None


class PolicyDraft(ApiModel):
    _omit_if_none = frozenset({"compiler"})

    draft_id: str
    card_id: str
    instruction: str
    """The C1 text verbatim, or exactly ``policies.FORM_INSTRUCTION`` for a form draft."""
    checks: list[RuleCheck]
    uncertainty_policy: UncertaintyChoice
    open_questions: list[str]
    """With no checks read, the first entry is ``policies.NO_CHECKS_QUESTION`` and C2 refuses the draft."""
    dry_run: DryRunResult
    compiler: Literal["llm", "form", "fallback"] | None = None


class Fulfilment(ApiModel):
    bought: int = Field(ge=0)
    requested: int = Field(ge=0)


class Confirmation(ApiModel):
    """A remembered customer yes ("things you've confirmed"); names are untrusted text."""

    rule_text: str
    merchant_name: str
    item_name: str


class MandateUsage(ApiModel):
    _omit_if_none = frozenset({"confirmations"})

    per_order_limit_chf: float | None
    period_limit_chf: float | None
    period_days: int | None
    period_spent_chf: float
    period_window_start: Timestamp
    pending_chf: float
    fulfilment: Fulfilment | None = None
    confirmations: list[Confirmation] | None = None
    as_of: Timestamp


class PassportSummary(ApiModel):
    """The mandate's passport, latest version (docs/passport.md)."""

    passport_id: str
    version: int = Field(ge=1)
    issued_at: Timestamp
    devices_count: int = Field(ge=0)


class Mandate(ApiModel):
    _omit_if_none = frozenset({"usage", "passport"})

    mandate_id: str
    card_id: str
    instruction: str
    """Its draft's ``instruction``, unchanged by C4; never the joined check texts."""
    checks: list[RuleCheck]
    uncertainty_policy: Literal["ask", "decline", "approve"]
    open_questions: list[str]
    status: Literal["active", "revoked"]
    confirmed_at: Timestamp
    usage: MandateUsage | None = None
    passport: PassportSummary | None = None


class Evidence(ApiModel):
    _omit_if_none = frozenset({"source"})

    rule: str
    outcome: Literal["pass", "fail", "uncertain", "info"]
    detail: str
    source: Literal["policy", "ledger", "history", "merchant_text", "model"] | None = None


class DecisionMerchant(ApiModel):
    merchant_id: str
    name: str


class DecisionItem(ApiModel):
    item_name: str
    quantity: int = Field(ge=1)
    unit_price: float
    currency: str
    item_details: str


class Uncertainty(ApiModel):
    note: str


class InjectionFlag(ApiModel):
    flagged: Literal[True]
    reason: str


class Related(ApiModel):
    authorization_id: str
    relation: Literal["requote_of", "duplicate_of", "retry_of", "split_of"]


class Session(ApiModel):
    trust: Literal["normal", "elevated", "frozen"]
    note: str


class MerchantMeta(ApiModel):
    category: str
    country: str
    familiar: bool
    prior_approvals_on_card: int = Field(ge=0)
    prior_approvals_other_cards: int = Field(ge=0)


_VALID_STATES: frozenset[tuple[str, str | None, str]] = frozenset(
    {
        ("approved", None, "final"),
        ("stopped", None, "final"),
        ("uncertain", "pending", "pending_human"),
        ("uncertain", "approved", "final"),
        ("uncertain", "declined", "final"),
        ("uncertain", "expired", "final"),
    }
)


class Confirmable(ApiModel):
    """A step-up decided by one restriction no data can check (field ``unverifiable``).
    Approving it is remembered for this shop and item; ``phrase`` is the rule's value."""

    rule_id: str
    phrase: str


class PolicyApplied(ApiModel):
    """The policy this decision was checked against (api-contract §2): our confirmed
    mandate (``confirmed``), or the platform mandate's own rules when no confirmed policy
    was bound to it (``platform``). The checks are what decided, whatever the card holds now."""

    mandate_id: str
    source: Literal["confirmed", "platform"]
    checks: list[RuleCheck]


OPERATOR_ONLY_EVIDENCE = frozenset({"ledger_mismatch"})
"""Evidence rules C6 sends only with ``?operator=1`` (api-contract §3.4): they stay in the
stored decision and the decision posted to Viseca, but the customer never sees them."""


class Decision(ApiModel):
    """One purchase decision. Only the combinations of §3.1a are valid."""

    _omit_if_none = frozenset(
        {
            "deadline_at",
            "merchant_meta",
            "engine_version",
            "latency_ms",
            "explanation_source",
            "resolved_by",
            "run_id",
            "run_started_at",
            "policy_applied",
            "receipt_id",
        }
    )

    authorization_id: str
    customer_id: str
    card_id: str
    decision: Literal["approved", "stopped", "uncertain"]
    uncertain_outcome: Literal["pending", "expired", "approved", "declined"] | None
    status: Literal["final", "pending_human"]
    reason_codes: list[str]
    message: str
    uncertainty: Uncertainty | None
    occurred_at: Timestamp
    merchant: DecisionMerchant
    amount: float
    currency: str
    billing_amount_chf: float
    items: list[DecisionItem]
    injection_flag: InjectionFlag | None
    evidence: list[Evidence]
    order_returnable: ReturnTerm
    delivery_by: date | None
    deadline_at: Timestamp | None = None

    counterfactual: str | None = None
    related: Related | None = None
    session: Session | None = None
    merchant_meta: MerchantMeta | None = None
    engine_version: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    explanation_source: Literal["template", "model"] | None = None
    resolved_by: Literal["customer", "timeout"] | None = None
    confirmable: Confirmable | None = None
    run_id: str | None = None
    run_started_at: Timestamp | None = None
    policy_applied: PolicyApplied | None = None
    would_approve_if: list[dict[str, Any]] | None = None
    """The counterfactual structured (declines only): ``{field, operator, value, scope?}``,
    ``{remove_items: [...]}`` or ``{requires: ...}`` per failing rule (docs/passport.md)."""
    receipt_id: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Decision:
        state = (self.decision, self.uncertain_outcome, self.status)
        if state not in _VALID_STATES:
            raise ValueError(f"invalid decision/uncertain_outcome/status combination {state}")
        if (self.status == "pending_human") != (self.deadline_at is not None):
            raise ValueError("deadline_at is required on pending_human and absent otherwise")
        return self


class ReplayStatus(ApiModel):
    scenario_id: str
    card_id: str
    delivered: int = Field(ge=0)
    total: int = Field(ge=0)
    running: bool
    next_at: Timestamp | None


class LiveRun(ApiModel):
    """D3, D4, D7. ``customer_id`` / ``customer_name``: who holds ``card_id`` (the card the
    platform's fixture profile runs the scenario on), once known."""

    _omit_if_none = frozenset({"customer_id", "customer_name"})

    run_id: str
    scenario_id: str
    card_id: str
    mandate_id: str
    state: Literal["starting", "running", "done", "error"]
    delivered: int = Field(ge=0)
    decided: int = Field(ge=0)
    pending_human: int = Field(ge=0)
    total: int = Field(ge=0)
    worker_ok: bool
    last_error: str | None
    customer_id: str | None = None
    customer_name: str | None = None


class ScenarioProfile(ApiModel):
    """D8: the customer and card a scenario runs on, and who said so (``pack``: the local
    data pack's authorities; the others: the platform, see ``store.schema.ScenarioProfile``)."""

    customer_id: str
    name: str
    card_id: str
    profile_id: str | None
    source: Literal["pack", "bootstrap", "run", "authorization"]


class Scenario(ApiModel):
    """D8. ``served``: the platform serves it now; ``profile`` null until a bootstrap
    profile or a run of it names its card; ``active_run_id``: a run of it still in
    progress (running, or with purchases still open at the platform), else null."""

    scenario_id: str
    scenario_name: str
    cardholder_instruction: str
    served: bool
    profile: ScenarioProfile | None
    active_run_id: str | None


class LedgerSnapshotEntry(ApiModel):
    authorization_id: str
    occurred_at: Timestamp
    decision: Literal["approved", "stopped", "uncertain"]
    counted_chf: float
    note: str


class LedgerSnapshot(ApiModel):
    card_id: str
    mandate_id: str
    entries: list[LedgerSnapshotEntry]
    period_spent_chf: float
    frozen: bool


# §1.0 envelopes ----------------------------------------------------------------------


class CustomersResponse(ApiModel):
    """C12."""

    customers: list[Customer]


class AccountsResponse(ApiModel):
    """C10."""

    accounts: list[Account]


class DecisionsResponse(ApiModel):
    """C6, newest first; ``OPERATOR_ONLY_EVIDENCE`` rows only with ``?operator=1``."""

    decisions: list[Decision]


class PolicyResponse(ApiModel):
    """C3."""

    mandate: Mandate | None


# §1.1 / §1.2 request bodies ----------------------------------------------------------


class PolicyDraftRequest(ApiModel):
    """C1: exactly one of ``instruction`` or ``form`` (422 on neither or both)."""

    _omit_if_none = frozenset({"instruction", "form"})

    instruction: str | None = Field(default=None, min_length=1)
    form: FormInput | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> PolicyDraftRequest:
        if (self.instruction is None) == (self.form is None):
            raise ValueError("send exactly one of instruction or form")
        return self


class ConfirmDraftRequest(ApiModel):
    """C2: ``checks`` are accepted ids; edited text is ignored."""

    checks: list[RuleCheck]
    uncertainty_policy: UncertaintyChoice
    open_questions: list[str]


class TightenRequest(ApiModel):
    """C4: a pure addition; ``uncertainty_policy`` may only move to ``decline``."""

    _omit_if_none = frozenset({"uncertainty_policy"})

    add_checks: list[RuleCheck]
    uncertainty_policy: Literal["decline"] | None = None


class ResolveRequest(ApiModel):
    """C8: the real customer's answer to a step-up."""

    decision: Literal["approve", "decline"]


class ReplayRestartRequest(ApiModel):
    """D2."""

    _omit_if_none = frozenset({"speed_ms"})

    scenario_id: str
    card_id: str
    speed_ms: int | None = Field(default=None, ge=0)


class CreateRunRequest(ApiModel):
    """D3. ``force``: start even while the scenario (or another) has a run in progress."""

    _omit_if_none = frozenset({"force"})

    scenario_id: str
    card_id: str
    force: bool | None = None


class ScenariosResponse(ApiModel):
    """D8."""

    scenarios: list[Scenario]


class SoftSignalsToggle(ApiModel):
    """D5 request and response body."""

    enabled: bool


class SoftSignalsState(ApiModel):
    """D5 GET: whether the models run now, for live runs and for the offline replay. The
    two differ until an operator sets D5 (api-contract §3.7)."""

    live: bool
    replay: bool


# Passport (docs/passport.md) ------------------------------------------------------------


class Device(ApiModel):
    """A browser or phone that may control a card. ``label`` is the customer's own text."""

    device_id: str
    card_id: str
    label: str
    status: Literal["pending", "enrolled", "removed"]
    enrolled_at: Timestamp | None
    enrolled_by_device_id: str | None
    removed_at: Timestamp | None
    last_seen_at: Timestamp


class DevicesResponse(ApiModel):
    devices: list[Device]


class EnrolDeviceRequest(ApiModel):
    public_key_jwk: dict[str, Any]
    label: str | None = Field(default=None, max_length=200)


class DeviceReset(ApiModel):
    """Operator reset (``/api/dev/devices/reset/{card}``): how many devices were removed."""

    card_id: str
    removed: int = Field(ge=0)


class EnrolDeviceResponse(ApiModel):
    device_id: str
    status: Literal["pending", "enrolled", "removed"]


class PassportVersion(ApiModel):
    version: int = Field(ge=1)
    issued_at: Timestamp
    reason: str


class Passport(ApiModel):
    """The latest signed version of the card's passport, and every version's reason."""

    passport_id: str
    version: int = Field(ge=1)
    document: dict[str, Any]
    signature: str
    key_id: str
    versions: list[PassportVersion]


class ReceiptSignature(ApiModel):
    """An earlier signature of a receipt, before its step-up was answered."""

    document: dict[str, Any]
    signature: str
    key_id: str
    signed_at: str


class Receipt(ApiModel):
    receipt_id: str
    document: dict[str, Any]
    signature: str
    key_id: str
    history: list[ReceiptSignature]


class PublicKey(ApiModel):
    key_id: str
    algorithm: Literal["ed25519"]
    public_key_pem: str
    active: bool


class KeysResponse(ApiModel):
    keys: list[PublicKey]


class VerifyRequest(ApiModel):
    """A document with its signature and key id, or the id of a stored one to check."""

    document: dict[str, Any] | None = None
    signature: str | None = None
    key_id: str | None = None
    passport_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    receipt_id: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> VerifyRequest:
        forms = [self.document is not None, self.passport_id is not None, self.receipt_id is not None]
        if sum(forms) != 1:
            raise ValueError("send a document with signature and key_id, or passport_id (and version), or receipt_id")
        if self.document is not None and (self.signature is None or self.key_id is None):
            raise ValueError("a document needs its signature and key_id")
        return self


class VerifyResult(ApiModel):
    """``valid`` when OneGuard's key signed exactly this document. ``document`` is the one
    checked (the stored one for an id), so a verify page can show who holds it."""

    valid: bool
    document_type: Literal["passport", "receipt"] | None
    key_id: str | None
    issued_at: str | None
    reason: str
    document: dict[str, Any] | None = None
    current: bool | None = None
    """For a passport: this is its latest version and it is not revoked. None for a receipt."""


# §3.8 errors --------------------------------------------------------------------------

ErrorCode = Literal[
    "not_found",
    "validation",
    "draft_confirmed",
    "lint_failed",
    "not_pure_addition",
    "not_awaiting_answer",
    "window_closed",
    "upstream_unavailable",
    "compiler_timeout",
    "internal",
    "runs_disabled",
    "run_active",
    "device_signature_required",
    "device_not_enrolled",
    "signature_invalid",
    "replay",
    "last_device",
    "device_state",
    "forbidden",
]


class ErrorBody(ApiModel):
    _omit_if_none = frozenset({"detail"})

    code: ErrorCode
    message: str
    detail: dict[str, Any] | None = None


class ErrorResponse(ApiModel):
    """``{ error: { code, message, detail? } }``; C2 lint_failed puts ``missing`` in detail."""

    error: ErrorBody
