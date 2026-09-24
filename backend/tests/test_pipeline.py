"""``pipeline.decide_event`` wiring that no lane's own tests see.

Shop familiarity (Q7, C9) is not in the event: the pipeline sets ``facts.merchant_known``
(customer level) and ``facts.merchant_known_on_card`` from the LedgerView before
``evaluate_rules``, so rules, protections and warning signs all read the ledger's answer
and never ``build_facts``'s default.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from oneguard.engine import stubs
from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.engine.types import Facts, LedgerView, Policy, RuleResult
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store.history import StoreHistoryIndex

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


class FamiliarityLedger(InMemoryLedger):
    """The in-memory ledger, with the view's known-shop sets replaced and every call kept."""

    def __init__(self, known: set[str], known_on_card: set[str]) -> None:
        super().__init__()
        self.known, self.known_on_card = known, known_on_card
        self.view_calls: list[dict[str, Any]] = []

    def view(self, **kwargs: Any) -> LedgerView:
        self.view_calls.append(kwargs)
        return super().view(**kwargs).model_copy(
            update={"known_merchant_ids": self.known, "known_merchant_ids_on_card": self.known_on_card}
        )


@pytest.fixture(scope="module")
def event() -> dict:
    return build_events(Pack.load(), "SCEN0000")[0]


@pytest.mark.parametrize(
    ("customer_level", "card_level"),
    [(False, False), (True, False), (True, True)],
    ids=["new-shop", "known-on-another-card", "known-on-this-card"],
)
def test_shop_familiarity_comes_from_the_ledger_view_before_rules_run(
    event: dict, customer_level: bool, card_level: bool
) -> None:
    merchant_id = event["authorization"]["merchant"]["merchant_id"]
    ledger = FamiliarityLedger(
        known={merchant_id, "M_OTHER"} if customer_level else {"M_OTHER"},
        known_on_card={merchant_id} if card_level else set(),
    )
    seen: dict[str, Facts] = {}

    def build_facts(event: dict, history: Any) -> Facts:
        facts = stubs.build_facts(event, history)
        # the pipeline overwrites these whatever build_facts says
        seen["built"] = facts.model_copy(
            update={"merchant_known": not customer_level, "merchant_known_on_card": not card_level}
        )
        return seen["built"]

    def evaluate_rules(facts: Facts, policy: Policy) -> list[RuleResult]:
        seen["rules"] = facts
        return []

    def protections(facts: Facts, policy: Policy, view: LedgerView) -> list:
        seen["protections"] = facts
        return []

    def warning_signs(facts: Facts, view: LedgerView, policy: Policy) -> list:
        seen["warnings"] = facts
        return []

    ctx = PipelineContext(
        policy=Policy(
            mandate_id="TM_PIPE", status="active", instruction="x", rules=[], uncertainty_policy="ask"
        ),
        ledger=ledger,
        history=StoreHistoryIndex(),
        run_id="run_pipe",
        implementations={
            **stubs.STUBS,
            "build_facts": build_facts,
            "evaluate_rules": evaluate_rules,
            "protections": protections,
            "warning_signs": warning_signs,
        },
        now=lambda: NOW,
    )
    decide_event(event, ctx)

    auth = event["authorization"]
    (call,) = ledger.view_calls
    assert (call["run_id"], call["customer_id"], call["card_id"], call["at"]) == (
        "run_pipe", event["mandate"]["customer_id"], auth["card_id"], seen["built"].timestamp,
    )  # fmt: skip
    for stage in ("rules", "protections", "warnings"):
        facts = seen[stage]
        assert (facts.merchant_known, facts.merchant_known_on_card) == (customer_level, card_level), stage
