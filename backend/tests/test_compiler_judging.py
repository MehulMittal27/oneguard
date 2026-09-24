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
4. Five free-text sentences from the P1 review (tests/fixtures/compiler/review_responses.yaml):
   the parser's exact typed rules, and the recorded response ships ``compiler: llm``.
5. What the LLM path takes from the customer's words, not the model: the boundary (T3),
   "X each" per purchase, and excluded things no category holds.
6. "one delivery a day" is a purchase count (cart.purchases_in_period), never a CHF period
   limit: SCEN0113 compiled on both paths declines a second dinner the same simulated day.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.draft import RuleSpec, rule_text
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
        ("cart.purchases_in_period", "<=", 1, None, "period", 1, "decline"),  # "one delivery a day"
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


# --- P1 review of #48: five free-text sentences -----------------------------------------
REVIEW_PATH = Path(__file__).parent / "fixtures" / "compiler" / "review_responses.yaml"
REVIEW = yaml.safe_load(REVIEW_PATH.read_text(encoding="utf-8"))
REVIEW_EXPECTED: dict[str, list[tuple]] = {
    "S1": [  # "Buy groceries but not more than 120 CHF"
        (BILL, "<=", 120, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("groceries",), None, None, None, "decline"),
    ],
    "S2": [  # "groceries only, max CHF 120 per order and 300 a week": 300 is CHF, a 7-day limit
        (BILL, "<=", 120, "CHF", "purchase", None, "decline"),
        (BILL, "<=", 300, "CHF", "period", 7, "decline"),
        (CAT, "in", ("groceries",), None, None, None, "decline"),
    ],
    "S3": [  # "clothing under 250 francs from shops I know": under is "<"
        (BILL, "<", 250, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("clothing",), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
    ],
    "S4": [  # "electronics up to CHF 300 each at known retailers": each purchase; session checks always run
        (BILL, "<=", 300, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("electronics",), None, None, None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
    ],
    "S5": [  # "one meal delivery a day, CHF 40 including delivery, never at the weekend"
        (BILL, "<=", 40, "CHF", "purchase", None, "decline"),  # the total already includes delivery
        (CAT, "in", ("dining", "food_delivery"), None, None, None, "decline"),
        ("authorization.weekday", "not_in", ("sat", "sun"), None, None, None, "decline"),
        ("cart.purchases_in_period", "<=", 1, None, "period", 1, "decline"),  # "one meal delivery a day"
    ],
}


def test_review_sentences_are_recorded():
    assert [e["label"] for e in REVIEW] == sorted(REVIEW_EXPECTED)


@pytest.mark.parametrize("entry", REVIEW, ids=[e["label"] for e in REVIEW])
def test_the_parser_reads_every_review_restriction(entry, history):
    draft = parse(entry["instruction"], history, "", TODAY)
    assert sorted(map(_key, draft.rules), key=repr) == sorted(REVIEW_EXPECTED[entry["label"]], key=repr)
    assert draft.open_questions == [] and draft.uncertainty_policy == "ask"


@pytest.mark.parametrize("entry", REVIEW, ids=[e["label"] for e in REVIEW])
def test_the_recorded_review_response_ships_llm(entry, history):
    instruction = entry["instruction"]
    draft = compile_instruction(instruction, history, "", Scripted(entry["response"]), today=TODAY)
    assert draft.compiler == "llm"
    floor = parse(instruction, history, "", TODAY)
    assert coverage(floor.model_copy(update={"rules": draft.rules})) >= coverage(floor)
    assert all(r.on_fail == "decline" for r in draft.rules)  # no "ask me if it changes" clause
    assert draft.open_questions == []


def test_counted_items_keep_a_per_item_cap():
    """"X each" is per item only when the items are counted (M13(a) tickets)."""
    draft = parse("Buy two concert tickets, max CHF 90 each, from the official ticket seller")
    assert [(r.field, r.value) for r in draft.rules if r.field.endswith("_chf")] == [("items[].unit_price_chf", 90)]


def _response(*rules: dict) -> dict:
    return {"uncertainty_policy": "ask", "requested_item": None, "nothing_extra": False, "rules": list(rules),
            "open_questions": []}


def _raw(field: str, operator: str, words: str, **values: Any) -> dict:
    return {"field": field, "operator": operator, "value_number": None, "value_text": None, "value_list": None,
            "value_from": "literal", "currency": None, "scope": None, "period_days": None, "words": words,
            "source": "exact", "on_fail": "decline"} | values


def test_the_llm_boundary_is_the_customers_word(history):
    """T3 on the LLM path: a model that reads "under 250 francs" as "<=" ships "<"."""
    instruction = "clothing under 250 francs from shops I know"
    read = read_with_llm(instruction, Scripted(_response(
        _raw(BILL, "<=", "under 250 francs", value_number=250, currency="CHF", scope="purchase"),
        _raw(CAT, "in", "clothing", value_list=["clothing"]),
        _raw(KNOWN, "=", "shops I know", value_text="true"),
    )), history, "", TODAY)
    assert [(r.field, r.operator) for r in read.rules if r.field == BILL] == [(BILL, "<")]


def test_each_without_counted_items_is_per_purchase_on_the_llm_path(history):
    """A model that reads "electronics up to CHF 300 each" as a per-item limit ships the
    parser's per-order cap; counted items ("two tickets, max CHF 90 each") stay per item."""
    each = _raw("items[].unit_price_chf", "<=", "up to CHF 300 each", value_number=300, currency="CHF",
                scope="purchase")
    read = read_with_llm(REVIEW[3]["instruction"], Scripted(_response(each)), history, "", TODAY)
    assert [(r.field, r.scope) for r in read.rules] == [(BILL, "purchase")]
    tickets = "Buy two concert tickets, max CHF 90 each, from the official ticket seller"
    read = read_with_llm(tickets, Scripted(_response(each | {"words": "max CHF 90 each", "value_number": 90})),
                         history, "", TODAY)
    assert [r.field for r in read.rules] == ["items[].unit_price_chf"]


def test_an_excluded_thing_no_category_holds_is_unverifiable(history):
    """"No alcohol, no gift cards": the model lists "alcohol" as a category; the categories stay
    excluded and "No alcohol" becomes a restriction no data can check, never a dropped rule."""
    instruction = SERVED["SCEN0117"]
    read = read_with_llm(instruction, Scripted(_response(
        _raw(CAT, "not_in", "No alcohol, no gift cards, no cosmetics",
             value_list=["alcohol", "gift_card", "cosmetics"]),
    )), history, "", TODAY)
    assert [(r.field, r.operator, r.value) for r in read.rules] == [
        (CAT, "not_in", ["gift_card", "cosmetics"]), ("unverifiable", "=", "No alcohol")]
    assert read.open_questions == []


COUNT_PHRASES = [
    ("one delivery a day", "<=", 1, 1),
    ("Order dinner, one a day, max CHF 30", "<=", 1, 1),
    ("At most two orders a week, CHF 80 per order", "<=", 2, 7),
    ("Pizza once per week, max CHF 30", "<=", 1, 7),
    ("fewer than three orders a month, CHF 60 max", "<", 3, 30),
]


@pytest.mark.parametrize("instruction,op,count,days", COUNT_PHRASES, ids=[c[0] for c in COUNT_PHRASES])
def test_a_count_per_period_is_a_purchase_count(instruction, op, count, days, history):
    rules = [r for r in parse(instruction, history, "", TODAY).rules if r.field == "cart.purchases_in_period"]
    assert [(r.operator, r.value, r.scope, r.period_days, r.id) for r in rules] == [(op, count, "period", days, "C12-count")]


def test_an_amount_a_week_is_never_a_count(history):
    """"max CHF 120 per order and 300 a week": 300 is a CHF 7-day limit, not 300 orders."""
    draft = parse("groceries only, max CHF 120 per order and 300 a week", history, "", TODAY)
    assert not [r for r in draft.rules if r.field == "cart.purchases_in_period"]


def test_the_llm_path_takes_a_count_and_asks_without_a_window(history):
    """The model's "= 1" is a cap ("<="); a count with no window becomes a question, never a guess."""
    instruction = "one delivery a day, max CHF 40"
    count = _raw("cart.purchases_in_period", "=", "one delivery a day", value_number=1, scope="period", period_days=1)
    read = read_with_llm(instruction, Scripted(_response(count)), history, "", TODAY)
    assert [(r.field, r.operator, r.value, r.scope, r.period_days) for r in read.rules] == [
        ("cart.purchases_in_period", "<=", 1, "period", 1)]
    read = read_with_llm(instruction, Scripted(_response(count | {"scope": None, "period_days": None})),
                         history, "", TODAY)
    assert read.rules == [] and read.open_questions == ['Over how many days should "one delivery a day" apply?']


def test_a_count_is_linted_as_stated_and_is_not_an_amount_cap(history):
    from oneguard.compiler.lint import lint, lint_accepted

    draft = parse("one delivery a day", history, "", TODAY)
    issues = {i.code for i in lint(draft).issues}
    assert "invented_value" not in issues
    assert not lint_accepted(draft.rules, [r.id for r in draft.rules]).ok  # a count is no per-order cap
    invented = draft.model_copy(update={"rules": [draft.rules[0].model_copy(update={"value": 3, "period_days": 7})]})
    assert [i.code for i in lint(invented).issues if i.rule_id == "C12-count"] == ["invented_value"] * 2


def _dinner_history() -> StoreHistoryIndex:
    """The customer's usual dinner service, device and country, so only the rules decide."""
    from oneguard.engine.types import HistoryRow
    from tests.test_c9_no_history import CARD, NEW, T0

    return StoreHistoryIndex(rows=[HistoryRow(
        authorization_id="H_DINNER", customer_id=NEW, card_id=CARD, initiator_type="human",
        timestamp=T0 - timedelta(days=20), transaction_type="purchase", status="approved", amount=35.0,
        currency="CHF", billing_amount_chf=35.0, merchant_id="ME_DINNER", merchant_name="Dinner Service",
        merchant_category="food_delivery", merchant_country="CH", channel="ecommerce", recurring=False,
        customer_device_id="DVC-NEW", description="",
    )])  # fmt: skip


def _scen0113_response() -> dict:
    return next(e["response"] for e in RECORDED if e["scenario"] == "SCEN0113")


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0113_declines_a_second_dinner_the_same_day(path):
    """SCEN0113 end to end, compiled on each path: a CHF 30 weekday dinner is approved, a
    second one the same simulated day declines with period_count_exceeded."""
    from oneguard.engine.ledger_base import InMemoryLedger
    from oneguard.engine.types import Policy
    from oneguard.llm.provider import NullProvider
    from oneguard.pipeline import PipelineContext, decide_event, period_days_of
    from tests.test_c9_no_history import event

    dinners = _dinner_history()
    instruction = SERVED["SCEN0113"]
    provider = NullProvider() if path == "fallback" else Scripted(_scen0113_response())
    draft = compile_instruction(instruction, dinners, "", provider, today=TODAY)
    assert draft.compiler == path
    policy = Policy(mandate_id="TM_NEW", status="active", instruction=instruction,
                    uncertainty_policy=draft.uncertainty_policy, rules=draft.rules,
                    allowed_item_categories=draft.allowed_item_categories,
                    requires_known_shop=draft.requires_known_shop)
    assert period_days_of(policy) == 1
    ctx = PipelineContext(policy=policy, ledger=InMemoryLedger(history=dinners), history=dinners,
                          run_id=f"run-113-{path}", now=lambda: datetime(2026, 9, 25, 12, 0, tzinfo=UTC))

    def dinner(item: str, minutes: int) -> dict:  # Monday 10 Aug 2026, 12:00 Zurich + minutes
        ev = event("ME_DINNER", item, minutes=minutes, amount=30.0)
        ev["authorization"]["merchant"]["merchant_category"] = "food_delivery"
        ev["authorization"]["items"][0].update(item_category="food_delivery", item_name="Dinner")
        return ev

    first, _, _ = decide_event(dinner("IT_DINNER_1", 0), ctx)
    assert first.outcome == "approve", first
    second, explanation, _ = decide_event(dinner("IT_DINNER_2", 7 * 60), ctx)  # 19:00 the same day
    assert (second.outcome, second.reason_codes) == ("decline", ["period_count_exceeded"])
    assert explanation.message == ("Declined CHF 30.00: you allowed one order per day; one was already "
                                   "approved today at 12:00. Would approve from tomorrow at 12:00.")


def test_one_item_reads_in_the_singular():
    """SCEN0101 on the LLM path adds "one ordinary grocery item" as cart.quantity = 1."""
    spec = RuleSpec(field="cart.quantity", operator="=", value=1, words="one ordinary grocery item")
    assert rule_text(spec) == "Exactly 1 item"
