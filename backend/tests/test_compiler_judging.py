"""The 10 served judging instructions (docs/judging-pack.md), lane P4.

1. The fallback parser reads every restriction in each served instruction: the exact typed
   rules per scenario, one test per scenario (each is the instruction that revealed a
   pattern the parser lacked).
2. Every worked example in the LLM system prompt compiles, through the LLM path, to the
   same kinds of restriction the parser finds in the same sentence, so what the prompt
   teaches and what the floor rule demands never drift apart.
3. Recorded model responses for the 10 (tests/fixtures/compiler/judging_responses.yaml):
   each ships ``compiler: llm`` with the parser's kinds plus any extras, ``on_fail``
   only where the customer asked, and an open question only where the instruction states
   no per-order limit (listed in ``QUESTION_WHY``).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.lint import coverage
from oneguard.compiler.llm import EXAMPLES, read_with_llm
from oneguard.compiler.parser import parse
from oneguard.store.history import StoreHistoryIndex

ROOT = Path(__file__).resolve().parents[2]
PACK = (ROOT / "docs" / "judging-pack.md").read_text(encoding="utf-8")
SERVED = {m.group(1): m.group(2) for m in re.finditer(r"^\| (SCEN\d+) \|[^|]*\|[^|]*\| (.+?) \|$", PACK, re.MULTILINE)}
RECORDED_PATH = Path(__file__).parent / "fixtures" / "compiler" / "judging_responses.yaml"
TODAY = date(2026, 9, 1)
BILL = "authorization.billing_amount_chf"
KNOWN = "merchant.familiar_on_card"
CAT = "items[].item_category"
WEEK = ["mon", "tue", "wed", "thu", "fri"]

# Open questions a correct reading keeps, and why (no per-order limit is stated; T5).
QUESTION_WHY = {
    "SCEN0124": "only a per-night price is stated: is the order limit CHF 600 (3 nights)?",
    "SCEN0136": "only a monthly total is stated: what is the most one payment may cost?",
}

# (field, operator, value, currency, scope, period_days, on_fail) per served scenario.
EXPECTED: dict[str, dict[str, Any]] = {
    "SCEN0101": {"rules": [
        (BILL, "<=", 20, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("groceries",), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
    ]},
    "SCEN0104": {"item": "hiking boots", "rules": [
        (BILL, "<=", 200, "EUR", "purchase", None, "decline"),          # "Pay no more than EUR 200"
        ("items[].size_eu", "=", 46, None, None, None, "decline"),
        ("order.order_returnable", "=", "true", None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),               # "retailer I already know"
        ("merchant.merchant_country", "=", "AT", None, None, None, "decline"),
        ("merchant.merchant_category", "=", "sporting_goods", None, None, None, "decline"),  # "outdoor"
    ]},
    "SCEN0106": {"rules": [
        (BILL, "<=", 300, "CHF", "purchase", None, "decline"),          # "purchases up to CHF 300 each"
        (CAT, "in", ("electronics",), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
    ]},
    "SCEN0113": {"rules": [
        (BILL, "<=", 40, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("dining", "food_delivery"), None, None, None, "decline"),  # "dinners"
        ("authorization.weekday", "in", tuple(WEEK), None, None, None, "decline"),  # weeknight, never weekend
        ("cart.purchases_in_period", "<=", 1, None, "period", 1, "decline"),        # engine gap
        (KNOWN, "=", "true", None, None, None, "decline"),               # "my usual services"
    ]},
    "SCEN0117": {"rules": [
        (BILL, "<=", 100, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("groceries", "household"), None, None, None, "decline"),
        (CAT, "not_in", ("gift_card", "cosmetics"), None, None, None, "decline"),
        ("unverifiable", "=", "No alcohol", None, None, None, "decline"),  # wine is "groceries": engine gap
        (KNOWN, "=", "true", None, None, None, "decline"),
    ]},
    "SCEN0122": {"item": "camera lens", "extra": True, "rules": [
        (BILL, "<=", 900, "CHF", "purchase", None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
    ]},
    "SCEN0124": {"rules": [
        ("items[].unit_price_chf", "<=", 200, "CHF", "purchase", None, "decline"),  # "per night"
        (CAT, "in", ("hotel",), None, None, None, "decline"),
        (CAT, "not_in", ("travel",), None, None, None, "decline"),      # "No flights, no insurance"
        ("order.order_cancellable", "=", "true", None, None, None, "decline"),  # "refundable rate only"
        ("unverifiable", "=", "a hotel in Munich", None, None, None, "decline"),
        ("unverifiable", "=", "for 3 nights from 10 September to 13 September", None, None, None, "decline"),
    ]},
    "SCEN0130": {"item": "hiking boots", "extra": True, "rules": [
        (BILL, "<=", 180, "CHF", "purchase", None, "decline"),
        ("items[].size_eu", "=", 42, None, None, None, "decline"),
        ("order.return_window_days", ">=", 14, None, None, None, "decline"),
        ("merchant.merchant_category", "=", "sporting_goods", None, None, None, "decline"),
    ]},
    "SCEN0135": {"rules": [
        (BILL, "<=", 100, "CHF", "purchase", None, "decline"),
        (BILL, "<=", 250, "CHF", "period", 7, "decline"),              # "in any 7-day window"
        (CAT, "in", ("groceries", "household"), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),               # "supermarkets I already use"
        ("merchant.merchant_category", "=", "groceries", None, None, None, "decline"),  # "at supermarkets"
    ]},
    "SCEN0136": {"rules": [
        (BILL, "<", 80, "CHF", "period", 30, "decline"),
        (CAT, "in", ("subscriptions",), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),               # "current subscriptions", "no new services"
        ("unverifiable", "=", "no premium tiers", None, None, None, "decline"),
        ("unverifiable", "=", "no annual prepayments", None, None, None, "decline"),
        ("unverifiable", "=", "the price has not changed since last time", None, None, None, "ask"),
    ]},
}


def _key(rule) -> tuple:
    value = rule.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, list):
        value = tuple(value)
    return (rule.field, rule.operator, value, rule.currency, rule.scope, rule.period_days, rule.on_fail)


@pytest.fixture(scope="module")
def history() -> StoreHistoryIndex:
    return StoreHistoryIndex(rows=[])


def test_the_pack_has_the_10_served_instructions():
    assert sorted(SERVED) == sorted(EXPECTED)


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_the_parser_reads_every_restriction(scenario, history):
    expected = EXPECTED[scenario]
    draft = parse(SERVED[scenario], history, "", TODAY)
    assert sorted(map(_key, draft.rules), key=repr) == sorted(expected["rules"], key=repr)
    assert draft.requested_item == expected.get("item")
    assert draft.nothing_extra is expected.get("extra", False)
    assert draft.uncertainty_policy == "ask"
    assert bool(draft.open_questions) is (scenario in QUESTION_WHY), draft.open_questions


def test_never_at_the_weekend_is_an_exclusion():
    """The inverted reading this pack caught: "Never at the weekend" once meant weekends only."""
    draft = parse("Order dinner for CHF 30 or less. Never at the weekend.")
    assert [(r.field, r.operator, r.value) for r in draft.rules if r.field == "authorization.weekday"] == [
        ("authorization.weekday", "not_in", ["sat", "sun"])]


class Scripted:
    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        import jsonschema

        jsonschema.validate(self.response, schema)
        return self.response


@pytest.mark.parametrize("instruction,output", EXAMPLES, ids=[f"example-{i}" for i in range(len(EXAMPLES))])
def test_every_prompt_example_matches_the_parser(instruction, output, history):
    floor = parse(instruction, history, "", TODAY)
    read = read_with_llm(instruction, Scripted(output), history, "", TODAY)
    assert coverage(read) == coverage(floor)
    draft = compile_instruction(instruction, history, "", Scripted(output), today=TODAY)
    assert draft.compiler == "llm"


RECORDED = yaml.safe_load(RECORDED_PATH.read_text(encoding="utf-8")) if RECORDED_PATH.exists() else []


@pytest.mark.skipif(not RECORDED, reason="judging_responses.yaml not recorded yet")
@pytest.mark.parametrize("entry", RECORDED, ids=[e["scenario"] for e in RECORDED])
def test_the_recorded_response_ships_llm(entry, history):
    scenario, instruction = entry["scenario"], SERVED[entry["scenario"]]
    draft = compile_instruction(instruction, history, "", Scripted(entry["response"]), today=TODAY)
    assert draft.compiler == "llm"
    floor = parse(instruction, history, "", TODAY)
    shipped = floor.model_copy(update={"rules": draft.rules, "requested_item": draft.requested_item,
                                       "nothing_extra": draft.nothing_extra,
                                       "uncertainty_policy": draft.uncertainty_policy})
    assert coverage(shipped) >= coverage(floor)
    asking = sorted(r.field for r in draft.rules if r.on_fail == "ask")
    assert asking == (["unverifiable"] if scenario == "SCEN0136" else [])
    assert bool(draft.open_questions) is (scenario in QUESTION_WHY), draft.open_questions


def test_every_served_instruction_is_recorded():
    if not RECORDED:
        pytest.skip("judging_responses.yaml not recorded yet")
    assert sorted(e["scenario"] for e in RECORDED) == sorted(SERVED)
