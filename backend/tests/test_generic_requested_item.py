"""A requested item made of generic words only names no product (docs/decisions.md 2026-09-25).

The cloud gpt-4.1 reading of SCEN0000's instruction ("Buy one ordinary grocery item for CHF 20
or less ...", SCEN0101's too) set requested_item "ordinary grocery item", single_item and a
cart.quantity = 1 rule; C5 and C12-qty then matched no line of "Fresh produce selection" and
declined AU0001 (oracle approve). The LLM path now drops such an item as the parser does (no
requested item, no "one" count), and lint rejects a reading that keeps one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.lint import lint
from oneguard.compiler.parser import is_generic_item, parse
from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.engine.types import Policy
from oneguard.llm.provider import NullProvider
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex
from tests.test_compiler_judging import RECORDED, SERVED, Scripted

POLICIES = Path(__file__).parent / "fixtures" / "policies"
INSTRUCTIONS = {
    "SCEN0000": yaml.safe_load((POLICIES / "SCEN0000.yaml").read_text(encoding="utf-8"))["instruction"],
    "SCEN0101": SERVED["SCEN0101"],
}
MODEL = next(e["response"] for e in RECORDED if e["scenario"] == "SCEN0101")  # the recorded gpt-4.1 reading
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
CARD = "CA0001"


@pytest.fixture(scope="module")
def pack_and_history(tmp_path_factory: pytest.TempPathFactory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('generic') / 'oneguard.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        history = StoreHistoryIndex.load(s)
    engine.dispose()
    return Pack.load(), history


@pytest.mark.parametrize("item", ["ordinary grocery item", "Grocery Items!", "something", "essentials",
                                  "product", "stuff"])
def test_generic_words_only_name_no_product(item):
    assert is_generic_item(item)


@pytest.mark.parametrize("item", [None, "", "grocery bag", "hiking boots", "ordinary milk"])
def test_a_named_product_is_not_generic(item):
    assert not is_generic_item(item)


def test_the_model_reading_names_the_generic_item():
    assert MODEL["requested_item"] == "ordinary grocery item"
    assert any(r["field"] == "cart.quantity" and r["value_number"] == 1 for r in MODEL["rules"])


@pytest.mark.parametrize("scenario", sorted(INSTRUCTIONS))
def test_the_llm_path_drops_the_generic_item_as_the_parser_does(scenario, pack_and_history):
    _, history = pack_and_history
    instruction = INSTRUCTIONS[scenario]
    today = date(2026, 9, 1)
    draft = compile_instruction(instruction, history, CARD, Scripted(MODEL), today=today)
    floor = compile_instruction(instruction, history, CARD, NullProvider(), today=today)
    assert (draft.compiler, draft.requested_item, draft.single_item) == ("llm", None, False)
    assert not any(r.field in ("cart.quantity", "items[].quantity") for r in draft.rules)
    key = lambda r: (r.id, r.field, r.operator, r.value, r.currency, r.scope, r.on_fail)
    assert sorted(map(key, draft.rules), key=repr) == sorted(map(key, floor.rules), key=repr)
    assert (floor.requested_item, floor.single_item) == (None, False)


@pytest.mark.parametrize("scenario", sorted(INSTRUCTIONS))
def test_au0001_approves_with_the_model_reading(scenario, pack_and_history):
    pack, history = pack_and_history
    instruction = INSTRUCTIONS[scenario]
    draft = compile_instruction(instruction, history, CARD, Scripted(MODEL), today=date(2026, 9, 1))
    policy = Policy(mandate_id="TM_GENERIC", status="active", instruction=instruction,
                    uncertainty_policy=draft.uncertainty_policy, rules=draft.rules,
                    requested_item=draft.requested_item, allowed_item_categories=draft.allowed_item_categories,
                    requires_known_shop=draft.requires_known_shop, nothing_extra=draft.nothing_extra,
                    shop_type=draft.shop_type, single_item=draft.single_item)
    ctx = PipelineContext(policy=policy, ledger=InMemoryLedger(history=history), history=history,
                          run_id=f"generic-{scenario}", now=lambda: NOW)
    events = [e for e in build_events(pack, "SCEN0000", mandate_id=policy.mandate_id, now=NOW)
              if e["authorization"]["source_authorization_id"] == "AU0001"]
    assert len(events) == 1
    decision, explanation, _ = decide_event(events[0], ctx)
    assert decision.outcome == "approve", (decision.reason_codes, explanation.message)


def test_lint_rejects_a_generic_requested_item():
    instruction = INSTRUCTIONS["SCEN0000"]
    floor = parse(instruction, StoreHistoryIndex(rows=[]), "", date(2026, 9, 1))
    kept = floor.model_copy(update={"requested_item": "ordinary grocery item"})
    assert [(i.code, i.rule_id) for i in lint(kept).issues] == [("generic_requested_item", "C5")]
    assert lint(floor).ok
    named = floor.model_copy(update={"requested_item": "grocery bag"})
    assert lint(named).ok
