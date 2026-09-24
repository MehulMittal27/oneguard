"""The compiler's LLM path on the five public instructions, against recorded responses (lane P4).

Responses live in tests/fixtures/compiler/recorded_responses.yaml. Asserts, per
response: which reading ships (llm or fallback), the kinds of restriction it holds,
every rule's on_fail and the uncertainty setting. The reviewed SCEN0002-style run
(on_fail ask everywhere, shop type and requested item lost) must be rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.lint import coverage, lint, lint_against_floor
from oneguard.compiler.llm import SCHEMA
from oneguard.compiler.parser import parse
from oneguard.engine.types import HistoryRow
from oneguard.llm.provider import ProviderUnavailable
from oneguard.store.history import StoreHistoryIndex

RECORDED = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "compiler" / "recorded_responses.yaml").read_text(encoding="utf-8"))
CARD = "CA_T1"


class RecordedProvider:
    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        import jsonschema

        jsonschema.validate(self.response, schema)  # a recorded response is a valid provider answer
        return self.response


@pytest.fixture(scope="module")
def history() -> StoreHistoryIndex:
    return StoreHistoryIndex(rows=[HistoryRow(
        authorization_id="TR_T001", customer_id="CU_T1", card_id=CARD, initiator_type="human",
        timestamp=datetime(2026, 8, 10, 8, 0, tzinfo=UTC), transaction_type="purchase", status="approved",
        amount=21.9, currency="CHF", billing_amount_chf=21.9, merchant_id="ME_T1", merchant_name="Corner Grocer",
        merchant_category="groceries", merchant_country="CH", channel="ecommerce", recurring=False,
        customer_device_id="DVC_T1", description="Groceries")])


@pytest.mark.parametrize("entry", RECORDED, ids=[e["label"] for e in RECORDED])
def test_recorded_llm_response_compiles_as_expected(entry, history):
    draft = compile_instruction(entry["instruction"], history, CARD, RecordedProvider(entry["response"]))
    expect = entry["expect"]
    assert draft.compiler == expect["compiler"]
    assert draft.uncertainty_policy == expect["uncertainty_policy"]
    assert {r.on_fail for r in draft.rules} == {expect["on_fail"]}
    shipped = parse(entry["instruction"], history, CARD)  # coverage() works on ParsedDraft
    shipped = shipped.model_copy(update={
        "rules": draft.rules, "requested_item": draft.requested_item, "nothing_extra": draft.nothing_extra,
        "uncertainty_policy": draft.uncertainty_policy})
    assert coverage(shipped) == set(expect["kinds"])


def test_the_reviewed_run_ships_the_limit_as_a_decline(history):
    entry = next(e for e in RECORDED if e["label"] == "public_2_reviewed_run")
    draft = compile_instruction(entry["instruction"], history, CARD, RecordedProvider(entry["response"]))
    limit = next(r for r in draft.rules if r.field == "authorization.billing_amount_chf")
    # A CHF 252 order against this limit must decline, not ask.
    assert (limit.operator, limit.value, limit.on_fail) == ("<=", 200, "decline")
    assert draft.shop_type == "sporting_goods" and draft.requested_item == "road-running shoes"


def test_every_recorded_response_is_schema_valid():
    import jsonschema

    for entry in RECORDED:
        jsonschema.validate(entry["response"], SCHEMA)


# --- Lint: on_fail and the fallback floor ------------------------------------------------
def _with_on_fail_ask(instruction: str):
    draft = parse(instruction)
    return draft.model_copy(update={"rules": [r.model_copy(update={"on_fail": "ask"}) for r in draft.rules]})


def test_on_fail_ask_needs_the_customer_to_say_so():
    uncertain = _with_on_fail_ask("Buy groceries up to CHF 50. Ask me when uncertain.")
    assert {i.code for i in lint(uncertain).issues} == {"on_fail_not_stated"}  # one per rule
    changed = _with_on_fail_ask("Buy groceries up to CHF 50, and if the price differs, ask me.")
    assert lint(changed).ok


def test_the_floor_reports_every_lost_kind():
    instruction = RECORDED[2]["instruction"]  # shoes: C1, C5, C6, C7, C8
    floor = parse(instruction)
    thinner = floor.model_copy(update={
        "rules": [r for r in floor.rules if r.field != "merchant.merchant_category"], "requested_item": None})
    assert sorted(i.rule_id for i in lint_against_floor(thinner, floor)) == ["C5", "C8"]
    assert lint_against_floor(floor, floor) == []


def test_the_llm_may_add_what_the_parser_cannot_read(history):
    """German: the parser finds nothing, so a sound LLM reading ships."""
    instruction = "Kaufe Lebensmittel für höchstens CHF 80 pro Bestellung"
    response = {
        "uncertainty_policy": "ask", "requested_item": None, "nothing_extra": False, "open_questions": [],
        "rules": [{"field": "authorization.billing_amount_chf", "operator": "<=", "value_number": 80,
                   "value_text": None, "value_list": None, "value_from": "literal", "currency": "CHF",
                   "scope": "purchase", "period_days": None, "words": "höchstens CHF 80 pro Bestellung",
                   "source": "exact", "on_fail": "decline"},
                  {"field": "items[].item_category", "operator": "in", "value_number": None,
                   "value_text": None, "value_list": ["groceries"], "value_from": "literal", "currency": None,
                   "scope": None, "period_days": None, "words": "Lebensmittel", "source": "inferred",
                   "on_fail": "decline"}],
    }
    draft = compile_instruction(instruction, history, CARD, RecordedProvider(response))
    assert draft.compiler == "llm"
    assert {r.id for r in draft.rules} == {"C1", "C3"}


def test_the_llm_timing_out_still_ships_the_parser_reading(history):
    class Late:
        def complete_json(self, *args):
            raise ProviderUnavailable("did not answer within 8 s")

    draft = compile_instruction(RECORDED[2]["instruction"], history, CARD, Late())
    assert draft.compiler == "fallback" and draft.shop_type == "sporting_goods"
