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
7. "Weeknight dinners" is the evening as well (local_hour >= 17 and < 23, inferred), on both
   paths: the live run's Tue 01:15 and Wed 15:35 deliveries now decline.
8. A per-night price times the stated nights is the per-order cap (inferred, the per-night
   rule kept), on both paths: SCEN0124 compiles with no open question and C2 accepts it.
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
ALCOHOL = "items[].contains_alcohol"
HOUR = "authorization.local_hour"
WEEK = ["mon", "tue", "wed", "thu", "fri"]

# Open questions a correct reading keeps, and why (no per-order limit is stated; T5).
QUESTION_WHY = {
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
        (CAT, "in", ("sporting_goods",), None, None, None, "decline"),  # hiking boots, in the catalogue
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
        (HOUR, ">=", 17, None, None, None, "decline"),                  # "dinners": the evening
        (HOUR, "<", 23, None, None, None, "decline"),
    ]},
    "SCEN0117": {"rules": [
        (BILL, "<=", 100, "CHF", "purchase", None, "decline"),
        (CAT, "in", ("groceries", "household"), None, None, None, "decline"),
        (CAT, "not_in", ("gift_card", "cosmetics"), None, None, None, "decline"),
        (ALCOHOL, "=", "false", None, None, None, "decline"),  # "No alcohol": wine is "groceries"
        (KNOWN, "=", "true", None, None, None, "decline"),
    ]},
    "SCEN0122": {"item": "camera lens", "extra": True, "rules": [
        (BILL, "<=", 900, "CHF", "purchase", None, "decline"),
        (KNOWN, "=", "true", None, None, None, "decline"),
        (CAT, "in", ("photography",), None, None, None, "decline"),  # the catalogue's camera lens
    ]},
    "SCEN0124": {"rules": [
        ("items[].unit_price_chf", "<=", 200, "CHF", "purchase", None, "decline"),  # "per night"
        (CAT, "in", ("hotel",), None, None, None, "decline"),
        (CAT, "not_in", ("travel",), None, None, None, "decline"),      # "No flights, no insurance"
        ("order.order_cancellable", "=", "true", None, None, None, "decline"),  # "refundable rate only"
        ("unverifiable", "=", "a hotel in Munich", None, None, None, "decline"),
        ("unverifiable", "=", "for 3 nights from 10 September to 13 September", None, None, None, "decline"),
        (BILL, "<=", 600, "CHF", "purchase", None, "decline"),  # 3 nights at CHF 200: the order cap
    ]},
    "SCEN0130": {"item": "hiking boots", "extra": True, "rules": [
        (BILL, "<=", 180, "CHF", "purchase", None, "decline"),
        ("items[].size_eu", "=", 42, None, None, None, "decline"),
        ("order.return_window_days", ">=", 14, None, None, None, "decline"),
        ("merchant.merchant_category", "=", "sporting_goods", None, None, None, "decline"),
        (CAT, "in", ("sporting_goods",), None, None, None, "decline"),  # hiking boots, in the catalogue
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
        (BILL, "=", "last_price_at_shop", "CHF", "purchase", None, "ask"),  # "If a price changes, ask me"
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
    assert asking == ([BILL] if scenario == "SCEN0136" else [])
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


def test_excluded_alcohol_is_the_alcohol_rule_on_the_llm_path(history):
    """"No alcohol, no gift cards": the model lists "alcohol" as a category; the categories stay
    excluded and "No alcohol" becomes the per-line alcohol rule, never a dropped rule. A thing
    no field holds ("no tobacco") stays a restriction no data can check."""
    instruction = SERVED["SCEN0117"]
    read = read_with_llm(instruction, Scripted(_response(
        _raw(CAT, "not_in", "No alcohol, no gift cards, no cosmetics",
             value_list=["alcohol", "gift_card", "cosmetics"]),
    )), history, "", TODAY)
    assert [(r.field, r.operator, r.value, r.id) for r in read.rules] == [
        (CAT, "not_in", ["gift_card", "cosmetics"], "C4"), (ALCOHOL, "=", "false", "C4-alcohol")]
    assert read.open_questions == []
    tobacco = "Groceries up to CHF 50. No tobacco, no gift cards."
    read = read_with_llm(tobacco, Scripted(_response(
        _raw(CAT, "not_in", "No tobacco, no gift cards", value_list=["tobacco", "gift_card"]),
    )), history, "", TODAY)
    assert [(r.field, r.value) for r in read.rules] == [(CAT, ["gift_card"]), ("unverifiable", "No tobacco")]


def test_a_models_unverifiable_no_alcohol_is_the_alcohol_rule(history):
    """A model that still writes "no alcohol" as a restriction no data can check (the reading
    recorded before the alcohol fact existed) ships the alcohol rule: the check is real now."""
    instruction = SERVED["SCEN0117"]
    read = read_with_llm(instruction, Scripted(_response(
        _raw("unverifiable", "=", "No alcohol", value_text="no alcohol"),
    )), history, "", TODAY)
    assert [(r.field, r.operator, r.value, r.text) for r in read.rules] == [(ALCOHOL, "=", "false", "No alcohol")]


def test_a_models_unverifiable_price_clause_is_the_last_price_at_each_shop(history):
    """A model that still writes "If a price changes, ask me" as a restriction no data can check
    (the reading recorded before the field existed) ships the per-shop price rule."""
    instruction = SERVED["SCEN0136"]
    read = read_with_llm(instruction, Scripted(_response(
        _raw("unverifiable", "=", "If a price changes, ask me",
             value_text="the price has not changed since last time", on_fail="ask"),
    )), history, "", TODAY)
    assert [(r.id, r.field, r.operator, r.value, r.on_fail) for r in read.rules] == [
        ("C1-same", BILL, "=", "last_price_at_shop", "ask")]


def test_a_models_unverifiable_item_type_is_the_excluded_type(history):
    """"No flights, no insurance": a model that also writes "no insurance" as a restriction no
    data can check ships the travel type the parser reads, never a question on every booking."""
    instruction = SERVED["SCEN0124"]
    read = read_with_llm(instruction, Scripted(_response(
        _raw(CAT, "not_in", "No flights, no insurance", value_list=["travel"]),
        _raw("unverifiable", "=", "no insurance", value_text="no insurance"),
    )), history, "", TODAY)
    assert [(r.field, r.operator, r.value) for r in read.rules] == [(CAT, "not_in", ["travel"])]


def test_a_per_item_amount_is_per_item_only_where_the_parser_reads_it_so(history):
    """"One ordinary grocery item for CHF 20 or less": a model that reads the amount as a
    per-item limit ships the parser's per-order cap (tighter, and C2 needs one), not a
    rejected reading. "CHF 200 per night" stays per item."""
    instruction = SERVED["SCEN0101"]
    per_item = _raw("items[].unit_price_chf", "<=", "CHF 20 or less", value_number=20, currency="CHF",
                    scope="purchase")
    draft = compile_instruction(instruction, history, "", Scripted(_response(
        per_item, _raw(CAT, "in", "grocery item", value_list=["groceries"]),
        _raw(KNOWN, "=", "shop I use regularly", value_text="true"),
    ) | {"requested_item": "ordinary grocery item"}), today=TODAY)
    assert draft.compiler == "llm"
    assert [(r.field, r.operator, r.value, r.scope) for r in draft.rules if r.field.endswith("_chf")] == [
        (BILL, "<=", 20, "purchase")]
    nights = _raw("items[].unit_price_chf", "<=", "at most CHF 200 per night", value_number=200, currency="CHF",
                  scope="purchase")
    read = read_with_llm(SERVED["SCEN0124"], Scripted(_response(nights)), history, "", TODAY)
    assert [r.field for r in read.rules] == ["items[].unit_price_chf", BILL]  # and the stay's cap


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


def _scen0113_context(path: str):
    """SCEN0113 compiled on one path, as an active policy over the usual dinner service."""
    from oneguard.engine.ledger_base import InMemoryLedger
    from oneguard.engine.types import Policy
    from oneguard.llm.provider import NullProvider
    from oneguard.pipeline import PipelineContext, period_days_of

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
    return PipelineContext(policy=policy, ledger=InMemoryLedger(history=dinners), history=dinners,
                           run_id=f"run-113-{path}", now=lambda: datetime(2026, 9, 25, 12, 0, tzinfo=UTC))


def _dinner(item: str, minutes: int) -> dict:
    """A CHF 30 dinner delivery at Monday 10 Aug 2026, 12:00 Zurich + minutes."""
    from tests.test_c9_no_history import event

    ev = event("ME_DINNER", item, minutes=minutes, amount=30.0)
    ev["authorization"]["merchant"]["merchant_category"] = "food_delivery"
    ev["authorization"]["items"][0].update(item_category="food_delivery", item_name="Dinner")
    return ev


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0113_declines_a_second_dinner_the_same_day(path):
    """SCEN0113 end to end, compiled on each path: a CHF 30 weekday dinner is approved, a
    second one the same simulated day declines with period_count_exceeded."""
    from oneguard.pipeline import decide_event

    ctx = _scen0113_context(path)
    first, _, _ = decide_event(_dinner("IT_DINNER_1", 6 * 60), ctx)  # 18:00
    assert first.outcome == "approve", first
    second, explanation, _ = decide_event(_dinner("IT_DINNER_2", 8 * 60), ctx)  # 20:00 the same day
    assert (second.outcome, second.reason_codes) == ("decline", ["period_count_exceeded"])
    assert explanation.message == ("Declined CHF 30.00: You allowed one order per day; one was already "
                                   "approved today at 18:00.")
    assert explanation.counterfactual == "Would approve from tomorrow at 18:00."


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0113_declines_a_dinner_outside_the_evening(path):
    """The live run approved SCEN0113 deliveries on Tue 01:15 and Wed 15:35 (Zurich): "weeknight
    dinners" is the evening too, so both now fail the evening check, on either path."""
    from oneguard.pipeline import decide_event

    ctx = _scen0113_context(path)
    night = 13 * 60 + 15  # Tue 11 Aug 01:15
    afternoon = 2 * 24 * 60 + 3 * 60 + 35  # Wed 12 Aug 15:35
    for item, minutes, at in (("IT_NIGHT", night, "01:15"), ("IT_AFTERNOON", afternoon, "15:35")):
        decision, explanation, _ = decide_event(_dinner(item, minutes), ctx)
        assert (decision.outcome, decision.reason_codes, decision.deciding_ids) == (
            "decline", ["rule_not_met"], ["C12-hour"]), (item, explanation.message)
        assert explanation.message == f"Declined CHF 30.00: Placed at {at} Swiss time; you allowed from 17:00."
        assert explanation.counterfactual == "Would approve from 17:00."
    evening, _, _ = decide_event(_dinner("IT_EVENING", 2 * 24 * 60 + 7 * 60), ctx)  # Wed 19:00
    assert evening.outcome == "approve", evening


def test_the_evening_window_is_inferred_and_no_cap(history):
    """"Weeknight dinners" gives local_hour >= 17 and < 23 (source inferred) with plain texts;
    a stated hour wins on its side; lunch and an excluded dinner name no hours. The window is
    no amount cap and no invented number for lint."""
    from oneguard.compiler.lint import lint, lint_accepted

    draft = parse(SERVED["SCEN0113"], history, "", TODAY)
    hours = [(r.id, r.operator, r.value, r.source, r.text) for r in draft.rules if r.field == HOUR]
    assert hours == [("C12-hour", ">=", 17, "inferred", "Only in the evening: from 17:00 (Swiss time)"),
                     ("C12-hour-2", "<", 23, "inferred", "Only in the evening: before 23:00 (Swiss time)")]
    stated = parse("Order supper after 19:00, max CHF 30", history, "", TODAY)
    assert [(r.operator, r.value, r.source) for r in stated.rules if r.field == HOUR] == [
        (">=", 19, "exact"), ("<", 23, "inferred")]
    for instruction in ("Order lunch on weekdays only, under CHF 25", "No dinners, groceries up to CHF 50"):
        assert not [r for r in parse(instruction, history, "", TODAY).rules if r.field == HOUR]
    window = [r for r in draft.rules if r.field == HOUR]
    assert [i.code for i in lint_accepted(window, [r.id for r in window]).issues] == ["no_amount_cap"]
    assert lint(draft).ok, lint(draft).issues


def test_the_llm_path_takes_the_parsers_evening_window(history):
    """A model that guesses other evening hours, or none, ships the parser's window; an hour
    the customer wrote in digits stays the model's reading."""
    instruction = SERVED["SCEN0113"]
    guessed = _raw(HOUR, ">=", "dinners", value_number=18, source="inferred")
    for rules in ([guessed], []):
        read = read_with_llm(instruction, Scripted(_response(*rules)), history, "", TODAY)
        assert [(r.operator, r.value) for r in read.rules if r.field == HOUR] == [(">=", 17), ("<", 23)]
    stated = "Order dinner after 19:00, max CHF 30"
    read = read_with_llm(stated, Scripted(_response(_raw(HOUR, ">=", "after 19:00", value_number=19))),
                         history, "", TODAY)
    assert [(r.operator, r.value) for r in read.rules if r.field == HOUR] == [(">=", 19), ("<", 23)]


def test_one_item_reads_in_the_singular():
    """SCEN0101 on the LLM path adds "one ordinary grocery item" as cart.quantity = 1."""
    spec = RuleSpec(field="cart.quantity", operator="=", value=1, words="one ordinary grocery item")
    assert rule_text(spec) == "Exactly 1 item"


# --- 8. per-night price x nights = the order cap (SCEN0124) -------------------------------
def test_scen0124_has_the_order_cap_and_no_question_on_both_paths(history):
    from oneguard.compiler import lint_accepted_ids

    instruction = SERVED["SCEN0124"]
    response = next(e["response"] for e in RECORDED if e["scenario"] == "SCEN0124")
    for draft in (parse(instruction, history, "", TODAY),
                  compile_instruction(instruction, history, "", Scripted(response), today=TODAY)):
        cap = [r for r in draft.rules if r.field == BILL]
        assert [(r.id, r.operator, r.value, r.scope, r.source) for r in cap] == [("C1", "<=", 600, "purchase", "inferred")]
        assert cap[0].text == "Total at or below CHF 600 per order (3 nights at CHF 200 each)"
        assert any(r.field == "items[].unit_price_chf" and r.value == 200 for r in draft.rules)  # kept
        assert draft.open_questions == []
        assert lint_accepted_ids(draft.rules, [r.id for r in draft.rules]) == ([], [])  # C2 accepts it


def test_the_models_order_limit_question_is_settled_by_the_cap(history):
    nights = _raw("items[].unit_price_chf", "<=", "at most CHF 200 per night", value_number=200, currency="CHF",
                  scope="purchase")
    asked = _response(nights) | {"open_questions": [
        "No per-order limit stated: is the order limit CHF 600 (3 nights at CHF 200 each)?", "Which hotel?"]}
    read = read_with_llm(SERVED["SCEN0124"], Scripted(asked), history, "", TODAY)
    assert read.open_questions == ["Which hotel?"]
    assert [(r.field, r.value) for r in read.rules if r.field == BILL] == [(BILL, 600)]


@pytest.mark.parametrize(("instruction", "cap"), [
    ("Book a hotel for 2 nights, under CHF 150 per night", ("<", 300, "CHF")),
    ("Book a hotel for two nights, max EUR 120 per night", ("<=", 240, "EUR")),
    ("Book a hotel for 3 nights, at most CHF 200 per night, max CHF 500 per order", ("<=", 500, "CHF")),
    ("Book a hotel from 3 May to 5 May, at most CHF 150 per night", None),  # no count of nights stated
    ("Buy two concert tickets, max CHF 90 each", None),  # counted items keep their question
], ids=["under", "euro", "stated-cap-wins", "dates-only", "counted-items"])
def test_the_cap_is_the_per_night_price_times_the_stated_nights(instruction, cap, history):
    draft = parse(instruction, history, "", TODAY)
    caps = [(r.operator, r.value, r.currency) for r in draft.rules if r.field == BILL and r.scope == "purchase"]
    assert caps == ([cap] if cap else [])
