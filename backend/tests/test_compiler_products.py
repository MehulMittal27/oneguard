"""A requested product on both compiler paths (lane P4, T1).

"buy/order/get [a|an|the|my|one] <product>" is the requested item (C5). When the item
catalogue files the product under one item type, that type is an inferred C3 rule
(``allowed_item_categories``); when it files it under none, the customer is asked
"Which kind of item or shop counts as <product>?" and the C5 rule stays. Category words
("groceries") stay category rules. An LLM reading that drops the parser's requested item
is rejected for the parser's reading (lint floor, restriction_dropped C5).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.lint import lint_against_floor
from oneguard.compiler.llm import read_with_llm
from oneguard.compiler.parser import PRODUCT_WORDS, parse, product_categories
from oneguard.llm.provider import NullProvider
from oneguard.store.history import StoreHistoryIndex

ROOT = Path(__file__).resolve().parents[2]
SERVED_ITEMS = yaml.safe_load((Path(__file__).parent / "fixtures" / "compiler" / "served_items.yaml").read_text())
with (ROOT / "data" / "items.csv").open(encoding="utf-8") as f:
    LOCAL_ITEMS = [{"item_name": r["item_name"], "item_category": r["item_category"]} for r in csv.DictReader(f)]
CATALOGUE = {(i["item_name"], i["item_category"]) for i in SERVED_ITEMS + LOCAL_ITEMS}
BILL = "authorization.billing_amount_chf"
CAT = "items[].item_category"

BAG = "buy a bag max 500 chf"
GROCERIES = "buy me groceries"
BOOTS = "order hiking boots in size 44, max CHF 180"
MONITOR = ("Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. "
           "Do not add anything I did not ask for. Ask me when uncertain.")  # SCEN0004


@pytest.fixture(scope="module")
def history() -> StoreHistoryIndex:
    return StoreHistoryIndex(rows=[])


def _typed(draft) -> list[tuple]:
    return sorted(((r.field, r.operator, r.value if not isinstance(r.value, list) else tuple(r.value), r.source)
                   for r in draft.rules), key=repr)


class Scripted:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        return self.response


def _model(requested_item: str | None, *rules: dict[str, Any]) -> Scripted:
    return Scripted({"uncertainty_policy": "ask", "requested_item": requested_item, "nothing_extra": False,
                     "rules": list(rules), "open_questions": []})


def _raw(field: str, operator: str, words: str, **values: Any) -> dict[str, Any]:
    return {"field": field, "operator": operator, "value_number": None, "value_text": None, "value_list": None,
            "value_from": "literal", "currency": None, "scope": None, "period_days": None, "words": words,
            "source": "exact", "on_fail": "decline"} | values


# --- the catalogue backs every mapping ---------------------------------------------------
@pytest.mark.parametrize("pattern,category", PRODUCT_WORDS, ids=[c for _, c in PRODUCT_WORDS])
def test_every_product_word_names_catalogue_items_of_its_category_only(pattern, category):
    matched = {(name, cat) for name, cat in CATALOGUE if pattern.search(name)}
    assert matched, "a product word must name something the catalogue sells (never invented)"
    assert {cat for _, cat in matched} == {category}, matched


@pytest.mark.parametrize("item", ["boots", "shoes", "bag", "phone plan", "concert tickets"])
def test_a_product_the_catalogue_files_under_no_single_type_maps_to_none(item):
    assert product_categories(item) == []  # hiking boots are sporting goods, winter boots clothing


# --- the captain's three, on both paths --------------------------------------------------
def test_a_bag_is_the_requested_item_with_a_question(history):
    expected = [(BILL, "<=", 500, "exact")]
    question = "Which kind of item or shop counts as bag?"
    floor = parse(BAG, history, "")
    assert _typed(floor) == expected
    assert (floor.requested_item, floor.allowed_item_categories, floor.open_questions) == ("bag", None, [question])

    read = read_with_llm(BAG, _model("bag", _raw(BILL, "<=", "max 500 chf", value_number=500, currency="CHF",
                                                  scope="purchase")), history, "")
    assert _typed(read) == expected and read.requested_item == "bag" and read.open_questions == [question]
    draft = compile_instruction(BAG, history, "", _model("bag", _raw(BILL, "<=", "max 500 chf", value_number=500,
                                                                         currency="CHF", scope="purchase")))
    assert draft.compiler == "llm" and draft.open_questions == [question]


def test_groceries_stay_a_category_not_a_requested_item(history):
    draft = parse(GROCERIES, history, "")
    assert _typed(draft) == [(CAT, "in", ("groceries",), "exact")]
    assert draft.requested_item is None
    assert draft.open_questions == ["No amount stated: what is the most one purchase may cost?"]


def test_hiking_boots_are_the_requested_item_and_sporting_goods(history):
    expected = [(BILL, "<=", 180, "exact"), (CAT, "in", ("sporting_goods",), "inferred"),
                ("items[].size_eu", "=", 44, "exact")]
    floor = parse(BOOTS, history, "")
    assert _typed(floor) == sorted(expected, key=repr)
    assert (floor.requested_item, floor.allowed_item_categories, floor.open_questions) == (
        "hiking boots", ["sporting_goods"], [])

    # A model that names the item but no type: the parser's mapping adds it, no question.
    model = _model("hiking boots", _raw(BILL, "<=", "max CHF 180", value_number=180, currency="CHF", scope="purchase"),
                   _raw("items[].size_eu", "=", "in size 44", value_number=44))
    draft = compile_instruction(BOOTS, history, "", model)
    assert draft.compiler == "llm"
    assert draft.allowed_item_categories == ["sporting_goods"] and draft.open_questions == []


def test_the_product_question_is_the_parsers_on_the_llm_path(history):
    """Seen live: gpt-4.1 asked it about a camera lens (photography) and about "accessories"
    in an item-types phrase with no requested item. Neither ships; a bag's still does."""
    lens = "Buy the camera lens I chose for CHF 900 or less"
    model = _model("camera lens", _raw(BILL, "<=", "for CHF 900 or less", value_number=900, currency="CHF",
                                       scope="purchase"))
    model.response["open_questions"] = ["Which kind of item or shop counts as camera lens?"]
    read = read_with_llm(lens, model, history, "")
    assert (read.allowed_item_categories, read.open_questions) == (["photography"], [])

    types = "small electronics and accessories purchases up to CHF 300 each"
    model = _model(None, _raw(CAT, "in", "small electronics", value_list=["electronics"]),
                   _raw(BILL, "<=", "up to CHF 300 each", value_number=300, currency="CHF", scope="purchase"))
    model.response["open_questions"] = ["Which kind of item or shop counts as accessories?"]
    assert read_with_llm(types, model, history, "").open_questions == []


# --- the lint floor ------------------------------------------------------------------------
def test_a_reading_without_the_requested_item_falls_back(history):
    """"buy a bag": a model that reads only the amount lost C5, so the parser's reading ships."""
    model = _model(None, _raw(BILL, "<=", "max 500 chf", value_number=500, currency="CHF", scope="purchase"))
    read = read_with_llm(BAG, model, history, "")
    assert [(i.code, i.rule_id) for i in lint_against_floor(read, parse(BAG, history, ""))] == [
        ("restriction_dropped", "C5")]
    draft = compile_instruction(BAG, history, "", model)
    assert (draft.compiler, draft.requested_item) == ("fallback", "bag")


# --- SCEN0004 on the fallback path (the demo's AU0043 depends on it) --------------------------
def test_the_monitor_instruction_on_the_fallback_path_reads_the_item_and_nothing_extra(history):
    draft = compile_instruction(MONITOR, history, "CA0039", NullProvider())
    assert draft.compiler == "fallback"
    assert (draft.requested_item, draft.nothing_extra) == ("27-inch monitor", True)
    assert draft.allowed_item_categories == ["electronics"] and draft.open_questions == []
