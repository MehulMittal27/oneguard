"""Gate 0 contract: API models, interface names, stubs, provider, pipeline on stubs."""

from __future__ import annotations

import copy
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from oneguard.api import models as api
from oneguard.engine import interfaces, stubs
from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.engine.types import FactValue, Policy, Rule, RuleResult
from oneguard.llm.provider import (
    NullProvider,
    Provider,
    ProviderUnavailable,
    get_provider,
    provider_available,
)
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store.history import StoreHistoryIndex

REPO = Path(__file__).resolve().parents[2]
EXAMPLE_EVENT = REPO / "data" / "scenario_fixtures" / "example_authorization_request.json"
FRONTEND_FIXTURES = REPO / "frontend" / "src" / "mocks" / "fixtures"

# One example per shape in docs/api-contract.md §1.0, §1.1, §1.2, §2, §3.8, with every
# NEW field present. Each must validate and serialise back to exactly itself.
CARD = {"card_id": "CA0001", "card_type": "debit", "card_purpose": "everyday", "status": "active"}
CHECK = {
    "id": "amount",
    "text": "Total at or below CHF 120 per order",
    "source": "exact",
    "uncertainty": None,
    "kind": "amount",
}
FORM = {
    "per_order_limit_chf": 120,
    "period_limit_chf": 300,
    "period_days": 7,
    "categories": ["groceries"],
    "sellers_used_before_only": True,
    "uncertainty_policy": "ask",
}
DRY_RUN_EXAMPLE = {
    "occurred_at": "2026-07-30T10:04:00Z",
    "merchant_name": "Alpine Basket",
    "billing_amount_chf": 44.5,
    "outcome": "fit",
    "reason": "Within CHF 120.",
}
DRY_RUN = {
    "sample_size": 30,
    "would_violate": 3,
    "would_fit": 25,
    "would_ask": 2,
    "insight": "Most orders fit.",
    "examples": [DRY_RUN_EXAMPLE],
    "agent_history": {"attempts": 10, "approved": 9},
}
CONFIRMATION = {
    "rule_text": "from the official ticket seller",
    "merchant_name": "EventForge",
    "item_name": "Concert ticket",
}
USAGE = {
    "per_order_limit_chf": 120,
    "period_limit_chf": 300,
    "period_days": 7,
    "period_spent_chf": 44.5,
    "period_window_start": "2026-08-03T09:12:00Z",
    "pending_chf": 0,
    "fulfilment": {"bought": 0, "requested": 1},
    "confirmations": [CONFIRMATION],
    "as_of": "2026-08-10T09:12:00Z",
}
MANDATE = {
    "mandate_id": "mnd_1",
    "card_id": "CA0001",
    "instruction": "Keep each order at or below CHF 120.",
    "checks": [CHECK],
    "uncertainty_policy": "ask",
    "open_questions": [],
    "status": "active",
    "confirmed_at": "2026-09-24T10:00:00Z",
    "usage": USAGE,
}
EVIDENCE = {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 44.50 ≤ CHF 120.", "source": "policy"}
DECISION = {
    "authorization_id": "live_1",
    "customer_id": "CU0001",
    "card_id": "CA0001",
    "decision": "uncertain",
    "uncertain_outcome": "pending",
    "status": "pending_human",
    "reason_codes": ["return_terms_unknown"],
    "message": "The shop doesn't state a return policy.",
    "uncertainty": {"note": "Return terms unknown."},
    "occurred_at": "2026-08-10T09:12:00Z",
    "merchant": {"merchant_id": "ME0001", "name": "Alpine Basket"},
    "amount": 44.5,
    "currency": "CHF",
    "billing_amount_chf": 44.5,
    "items": [
        {
            "item_name": "Fresh produce selection",
            "quantity": 1,
            "unit_price": 38.5,
            "currency": "CHF",
            "item_details": "Seasonal fruit and vegetables",
        }
    ],
    "injection_flag": {"flagged": True, "reason": "Instructions found in the product text were ignored."},
    "evidence": [EVIDENCE],
    "order_returnable": "unknown",
    "delivery_by": "2026-08-11",
    "deadline_at": "2026-09-24T10:02:00Z",
    "counterfactual": "Would approve with a stated return window.",
    "related": {"authorization_id": "live_0", "relation": "requote_of"},
    "session": {"trust": "elevated", "note": "New device."},
    "merchant_meta": {
        "category": "groceries",
        "country": "CH",
        "familiar": True,
        "prior_approvals_on_card": 3,
        "prior_approvals_other_cards": 1,
    },
    "engine_version": "oneguard/0.0.0 signals=off",
    "latency_ms": 3.2,
    "explanation_source": "template",
}
RESOLVED_DECISION = {
    **{k: v for k, v in DECISION.items() if k != "deadline_at"},
    "uncertain_outcome": "expired",
    "status": "final",
    "resolved_by": "timeout",
}

EXAMPLES: dict[type[BaseModel], dict[str, Any]] = {
    api.Customer: {
        "customer_id": "CU0001",
        "name": "Alex Meier",
        "home_region": "Zurich region",
        "card_id": "CA0001",
        "scenario_ids": ["S1", "S2"],
        "live": True,
    },
    api.Card: CARD,
    api.Account: {
        "account_id": "AC0001",
        "customer_id": "CU0001",
        "account_type": "debit",
        "account_purpose": "daily_spending",
        "status": "active",
        "per_transaction_limit_chf": 1200.0,
        "monthly_limit_chf": 4500.0,
        "cards": [CARD],
    },
    api.RuleCheck: CHECK,
    api.FormInput: FORM,
    api.DryRunExample: DRY_RUN_EXAMPLE,
    api.AgentHistory: {"attempts": 10, "approved": 9},
    api.DryRunResult: DRY_RUN,
    api.PolicyDraft: {
        "draft_id": "draft_1",
        "card_id": "CA0001",
        "instruction": "Keep each order at or below CHF 120.",
        "checks": [CHECK],
        "uncertainty_policy": "ask",
        "open_questions": ["Which shops count as familiar?"],
        "dry_run": DRY_RUN,
        "compiler": "fallback",
    },
    api.Fulfilment: {"bought": 0, "requested": 1},
    api.Confirmation: CONFIRMATION,
    api.MandateUsage: USAGE,
    api.Mandate: MANDATE,
    api.Evidence: EVIDENCE,
    api.DecisionMerchant: DECISION["merchant"],
    api.DecisionItem: DECISION["items"][0],
    api.Uncertainty: DECISION["uncertainty"],
    api.InjectionFlag: DECISION["injection_flag"],
    api.Related: DECISION["related"],
    api.Session: DECISION["session"],
    api.MerchantMeta: DECISION["merchant_meta"],
    api.Decision: DECISION,
    api.ReplayStatus: {
        "scenario_id": "S1",
        "card_id": "CA0001",
        "delivered": 3,
        "total": 10,
        "running": True,
        "next_at": "2026-09-24T10:00:04Z",
    },
    api.LiveRun: {
        "run_id": "run_1",
        "scenario_id": "S1",
        "card_id": "CA0001",
        "mandate_id": "mnd_1",
        "state": "running",
        "delivered": 3,
        "decided": 3,
        "pending_human": 1,
        "total": 10,
        "worker_ok": True,
        "last_error": None,
    },
    api.LedgerSnapshotEntry: {
        "authorization_id": "live_1",
        "occurred_at": "2026-08-10T09:12:00Z",
        "decision": "approved",
        "counted_chf": 44.5,
        "note": "final approval",
    },
    api.LedgerSnapshot: {
        "card_id": "CA0001",
        "mandate_id": "mnd_1",
        "entries": [
            {
                "authorization_id": "live_1",
                "occurred_at": "2026-08-10T09:12:00Z",
                "decision": "approved",
                "counted_chf": 44.5,
                "note": "final approval",
            }
        ],
        "period_spent_chf": 44.5,
        "frozen": False,
    },
    api.CustomersResponse: {"customers": []},
    api.AccountsResponse: {"accounts": []},
    api.DecisionsResponse: {"decisions": [DECISION, RESOLVED_DECISION]},
    api.PolicyResponse: {"mandate": MANDATE},
    api.PolicyDraftRequest: {"instruction": "Keep each order at or below CHF 120."},
    api.ConfirmDraftRequest: {"checks": [CHECK], "uncertainty_policy": "ask", "open_questions": []},
    api.TightenRequest: {"add_checks": [CHECK], "uncertainty_policy": "decline"},
    api.ResolveRequest: {"decision": "approve"},
    api.ReplayRestartRequest: {"scenario_id": "S1", "card_id": "CA0001", "speed_ms": 4000},
    api.CreateRunRequest: {"scenario_id": "S1", "card_id": "CA0001"},
    api.SoftSignalsToggle: {"enabled": True},
    api.ErrorBody: {"code": "lint_failed", "message": "No per-order cap.", "detail": {"missing": ["amount"]}},
    api.ErrorResponse: {"error": {"code": "not_found", "message": "No such card."}},
}


def _api_models() -> set[type[BaseModel]]:
    return {
        obj
        for obj in vars(api).values()
        if inspect.isclass(obj) and issubclass(obj, api.ApiModel) and obj is not api.ApiModel
    }


def test_every_api_model_has_an_example() -> None:
    assert _api_models() == set(EXAMPLES)


@pytest.mark.parametrize("model", sorted(EXAMPLES, key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_api_model_round_trips_its_example(model: type[BaseModel]) -> None:
    example = EXAMPLES[model]
    assert model.model_validate(example).model_dump(mode="json") == example


@pytest.mark.parametrize("model", sorted(EXAMPLES, key=lambda m: m.__name__), ids=lambda m: m.__name__)
def test_api_model_forbids_extra_fields(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**EXAMPLES[model], "unexpected": 1})


def test_optional_fields_are_omitted_and_nullable_fields_are_null() -> None:
    minimal = {
        k: v
        for k, v in DECISION.items()
        if k not in {"merchant_meta", "engine_version", "latency_ms", "explanation_source"}
    }
    minimal.update(
        decision="approved",
        uncertain_outcome=None,
        status="final",
        uncertainty=None,
        injection_flag=None,
        counterfactual=None,
        related=None,
        session=None,
    )
    del minimal["deadline_at"]
    dumped = api.Decision.model_validate(minimal).model_dump(mode="json")
    assert dumped == minimal
    for key in ("uncertain_outcome", "uncertainty", "injection_flag", "counterfactual", "related", "session"):
        assert key in dumped and dumped[key] is None
    for key in ("deadline_at", "merchant_meta", "resolved_by", "latency_ms"):
        assert key not in dumped
    assert "kind" not in api.RuleCheck(id="a", text="t", source="exact", uncertainty=None).model_dump()
    usage = {k: v for k, v in USAGE.items() if k != "confirmations"}
    assert api.MandateUsage.model_validate(usage).model_dump(mode="json") == usage


@pytest.mark.parametrize(
    ("decision", "uncertain_outcome", "status", "deadline"),
    [
        ("approved", "pending", "final", False),
        ("stopped", None, "pending_human", True),
        ("uncertain", None, "final", False),
        ("uncertain", "pending", "final", False),
        ("uncertain", "pending", "pending_human", False),
        ("approved", None, "final", True),
    ],
)
def test_decision_rejects_states_outside_the_consistency_matrix(
    decision: str, uncertain_outcome: str | None, status: str, deadline: bool
) -> None:
    payload = {**DECISION, "decision": decision, "uncertain_outcome": uncertain_outcome, "status": status}
    if not deadline:
        payload.pop("deadline_at")
    with pytest.raises(ValidationError):
        api.Decision.model_validate(payload)


def test_timestamps_serialise_as_utc_z() -> None:
    usage = api.MandateUsage.model_validate({**USAGE, "as_of": "2026-08-10T11:12:00.5+02:00"})
    assert usage.model_dump(mode="json")["as_of"] == "2026-08-10T09:12:00Z"
    with pytest.raises(ValidationError):
        api.MandateUsage.model_validate({**USAGE, "as_of": "2026-08-10T09:12:00"})


@pytest.mark.parametrize("body", [{}, {"instruction": "x", "form": FORM}])
def test_policy_draft_request_needs_exactly_one_input(body: dict) -> None:
    with pytest.raises(ValidationError):
        api.PolicyDraftRequest.model_validate(body)


@pytest.mark.parametrize(
    ("fixture", "key", "model"),
    [
        ("decisions.json", "decisions", api.Decision),
        ("customers.json", "customers", api.Customer),
        ("accounts.json", "accounts", api.Account),
    ],
)
def test_frontend_mock_fixtures_validate(fixture: str, key: str, model: type[BaseModel]) -> None:
    rows = json.loads((FRONTEND_FIXTURES / fixture).read_text(encoding="utf-8"))[key]
    assert rows
    for row in rows:
        row = {k: v for k, v in row.items() if k != "mock"}
        if row.get("status") == "pending_human":
            row.setdefault("deadline_at", "2026-09-24T10:02:00Z")  # computed at read time in mocks
        model.model_validate(row)


# Engine interfaces ------------------------------------------------------------------------

EXPECTED_INTERFACES = {
    "build_facts",
    "evaluate_rules",
    "resolve_unknowns",
    "protections",
    "warning_signs",
    "soft_signals",
    "decide",
    "explain",
    "rewrite_explanation",
    "compile_instruction",
}


def test_every_interface_exists_and_raises_until_implemented() -> None:
    assert set(interfaces.INTERFACES) == EXPECTED_INTERFACES
    assert set(interfaces.IMPLEMENTATION_MODULES) == EXPECTED_INTERFACES
    for name, fn in interfaces.INTERFACES.items():
        assert getattr(interfaces, name) is fn
        with pytest.raises(NotImplementedError):
            fn(*[None] * len(inspect.signature(fn).parameters))


@pytest.mark.parametrize("name", sorted(EXPECTED_INTERFACES))
def test_stub_signature_matches_interface(name: str) -> None:
    assert inspect.signature(stubs.STUBS[name]) == inspect.signature(interfaces.INTERFACES[name])
    assert name in stubs.ACTIVE


def test_register_rejects_unknown_names() -> None:
    with pytest.raises(ValueError):
        interfaces.register("approve_everything")


def _real(name: str):
    def fn(*args: Any) -> str:
        return f"real {name}"

    return fn


def test_select_implementations() -> None:
    names = sorted(EXPECTED_INTERFACES)
    real = {"decide": _real("decide")}

    active, stubbed = stubs.select_implementations(None, real)
    assert active["decide"] is real["decide"]
    assert stubbed == frozenset(set(names) - {"decide"})

    active, stubbed = stubs.select_implementations("all", real)
    assert stubbed == frozenset(names)
    assert active["decide"] is stubs.STUBS["decide"]

    active, stubbed = stubs.select_implementations("decide, explain", real)
    assert active["decide"] is stubs.STUBS["decide"] and "decide" in stubbed

    with pytest.raises(RuntimeError):
        stubs.select_implementations("none", real)
    everything = {name: _real(name) for name in names}
    assert stubs.select_implementations("none", everything) == (everything, frozenset())

    with pytest.raises(ValueError):
        stubs.select_implementations("decide,approve_everything", real)


def test_fact_value_known_needs_a_value() -> None:
    with pytest.raises(ValidationError):
        FactValue[int](known=True, source="regex")
    assert FactValue[int](value=43, known=True, source="regex").value == 43
    assert FactValue[int](known=False, source="regex", detail="absent").value is None


def test_rule_value_is_never_a_boolean() -> None:
    base = {"id": "r", "field": "merchant.familiar_on_card", "operator": "=", "text": "t", "source": "exact"}
    assert Rule(**base, value="true").value == "true"
    with pytest.raises(ValidationError):
        Rule(**base, value=True)


# Provider ---------------------------------------------------------------------------------


def test_provider_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONEGUARD_LLM_PROVIDER", raising=False)
    assert isinstance(get_provider(), NullProvider)
    for name in ("null", "openai", "anthropic"):  # placeholders degrade to NullProvider
        provider = get_provider(name)
        assert isinstance(provider, Provider)
        assert not provider_available(provider)
        with pytest.raises(ProviderUnavailable):
            provider.complete_json({}, "system", "user", 1.0)
    monkeypatch.setenv("ONEGUARD_LLM_PROVIDER", "OpenAI")
    assert isinstance(get_provider(), NullProvider)
    with pytest.raises(ValueError):
        get_provider("gpt")
    assert not provider_available(None)


# Pipeline on stubs ------------------------------------------------------------------------


def _context(**overrides: Any) -> PipelineContext:
    history = StoreHistoryIndex()
    policy = Policy(
        mandate_id="mnd_1", status="active", instruction="stub", rules=[], uncertainty_policy="ask"
    )
    fields = {
        "policy": policy,
        "ledger": InMemoryLedger(history=history),
        "history": history,
        "run_id": "run_1",
        "implementations": stubs.STUBS,
        "stubbed": frozenset(stubs.STUBS),
        "now": lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
    }
    return PipelineContext(**{**fields, **overrides})


def _event() -> dict:
    return json.loads(EXAMPLE_EVENT.read_text(encoding="utf-8"))


def test_stub_pipeline_returns_a_valid_decision_for_the_example_event() -> None:
    ctx = _context()
    engine, explanation, decision = decide_event(_event(), ctx)

    assert engine.outcome == "step_up" and engine.reason_codes == ["stub"]
    assert explanation.message == "stub" and explanation.evidence
    assert isinstance(decision, api.Decision)
    wire = decision.model_dump(mode="json")
    assert api.Decision.model_validate(wire) == decision
    assert wire["authorization_id"] == "AU_EXAMPLE_0001"
    assert wire["customer_id"] == "CU_EXAMPLE_0001"
    assert (wire["decision"], wire["uncertain_outcome"], wire["status"]) == (
        "uncertain",
        "pending",
        "pending_human",
    )
    assert wire["deadline_at"] == "2026-09-24T10:02:00Z"
    assert wire["occurred_at"] == "2026-08-12T08:59:59Z"
    assert wire["reason_codes"] == ["stub"] and wire["evidence"]
    assert wire["order_returnable"] == "unknown" and wire["delivery_by"] is None
    for key in ("counterfactual", "related", "session", "merchant_meta", "engine_version", "latency_ms"):
        assert key in wire
    assert wire["explanation_source"] == "template"
    assert wire["engine_version"].endswith("stubs=all")
    assert ctx.ledger.get("AU_EXAMPLE_0001").reserved_chf == 20.0


def test_redelivery_returns_the_stored_result_and_counts_nothing() -> None:
    ctx = _context()
    first = decide_event(_event(), ctx)
    ctx.now = lambda: datetime(2026, 9, 24, 10, 1, tzinfo=UTC)
    second = decide_event(_event(), ctx)
    assert second == first
    assert len(ctx.ledger.entries) == 1


def test_stub_facts_convert_foreign_currency_with_the_event_rate() -> None:
    event = _event()
    auth = event["authorization"]
    auth.update(currency="EUR", amount=20.0, billing_amount_chf=19.0)
    auth["items"][0].update(currency="EUR", unit_price=18.30)
    facts = stubs.build_facts(event, StoreHistoryIndex())
    assert facts.items[0].unit_price_chf == 17.38  # 18.30 × 0.95 = 17.385, half-even
    assert facts.local_hour == 10 and facts.local_weekday == "wed"
    assert not facts.return_window_days.known


def test_tier2_is_skipped_without_a_provider_and_failures_are_contained() -> None:
    calls: list[str] = []

    def unknown_rules(facts, policy):
        return [RuleResult(rule_id="size", outcome="unknown", detail="no size", source="regex")]

    def exploding_tier2(facts, rules, provider, budget_s):
        calls.append("tier2")
        raise ProviderUnavailable("down")

    functions = {**stubs.STUBS, "evaluate_rules": unknown_rules, "resolve_unknowns": exploding_tier2}
    decide_event(copy.deepcopy(_event()), _context(implementations=functions))
    assert calls == []

    class Configured:
        def complete_json(self, schema, system, user, timeout_s):
            raise ProviderUnavailable("down")

    _, _, decision = decide_event(_event(), _context(implementations=functions, provider=Configured()))
    assert calls == ["tier2"]
    assert decision.status == "pending_human"
