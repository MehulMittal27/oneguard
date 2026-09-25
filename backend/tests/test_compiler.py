"""Policy compiler (lane P4): LLM path with a mocked provider, fallback parser, lint, dry-run.

Expected typed rules come from docs/acceptance-oracle.yaml: the hand-built policies in
tests/fixtures/policies for the five public instructions, and ``unseen_instructions``
for the four invented ones. Two readings follow later decisions than the oracle text:
"two tickets" is ``cart.quantity`` (api-contract §3.3, P2 PM decision: 1 line x 2 or
2 lines x 1), and "the birthday present I picked" is an ``unverifiable`` rule (the
engine's item matcher cannot recognise a present by name; the customer is asked).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.compiler.draft import ParsedDraft
from oneguard.compiler.lint import lint, lint_accepted
from oneguard.compiler.llm import read_with_llm
from oneguard.compiler.parser import parse
from oneguard.engine.interfaces import IMPLEMENTATIONS, load_implementations
from oneguard.engine.types import HistoryRow, Rule
from oneguard.llm.provider import NullProvider, ProviderUnavailable
from oneguard.store.history import StoreHistoryIndex

ROOT = Path(__file__).resolve().parents[2]
ORACLE = yaml.safe_load((ROOT / "docs" / "acceptance-oracle.yaml").read_text(encoding="utf-8"))
FIXTURES = sorted((Path(__file__).parent / "fixtures" / "policies").glob("*.yaml"))
PUBLIC = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in FIXTURES]
UNSEEN = {u["instruction"]: u for u in ORACLE["unseen_instructions"]}
CARD, CUSTOMER = "CA_T1", "CU_T1"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]

TICKETS = "Buy two concert tickets, max CHF 90 each, from the official ticket seller"
LUNCH = "Order lunch on weekdays only, under CHF 25"
PRESENT = "Buy the birthday present I picked from a Swiss shop, must arrive by Friday"
GYM = "Renew my gym membership, same price as last time, ask me if anything changed"


# --- History ---------------------------------------------------------------------------
def _row(n: int, when: datetime, chf: float, merchant: tuple[str, str, str, str], desc: str,
         initiator: str = "human") -> HistoryRow:
    mid, name, category, country = merchant
    return HistoryRow(
        authorization_id=f"TR_T{n:03d}", customer_id=CUSTOMER, card_id=CARD, initiator_type=initiator,
        timestamp=when, transaction_type="purchase", status="approved", amount=chf, currency="CHF",
        billing_amount_chf=chf, merchant_id=mid, merchant_name=name, merchant_category=category,
        merchant_country=country, channel="ecommerce", recurring=False, customer_device_id="DVC_T1",
        description=desc,
    )


GYM_SHOP = ("ME_T_GYM", "Alpine Fitness Club", "health", "CH")
GROCER = ("ME_T_GROCER", "Corner Grocer", "groceries", "CH")
TECH = ("ME_T_TECH", "Pixel Shop", "electronics", "DE")


@pytest.fixture(scope="module")
def history() -> StoreHistoryIndex:
    rows = [
        _row(1, datetime(2026, 6, 12, 9, 0, tzinfo=UTC), 55.00, GYM_SHOP, "Monthly gym membership"),
        _row(2, datetime(2026, 7, 12, 9, 0, tzinfo=UTC), 59.00, GYM_SHOP, "Monthly gym membership"),
        _row(3, datetime(2026, 7, 20, 17, 0, tzinfo=UTC), 18.40, GROCER, "Weekly groceries", initiator="agent"),
        _row(4, datetime(2026, 7, 25, 17, 0, tzinfo=UTC), 520.00, TECH, "Laptop"),
        # The card's simulated present: Monday 10 Aug 2026 ("by Friday" = Fri 14 Aug).
        _row(5, datetime(2026, 8, 10, 8, 0, tzinfo=UTC), 21.90, GROCER, "Groceries"),
    ]
    return StoreHistoryIndex(rows=rows)


def _hard(rule: Rule) -> tuple:
    value = rule.value if not isinstance(rule.value, float) or not rule.value.is_integer() else int(rule.value)
    return (rule.field, rule.operator, value, rule.currency, rule.scope, rule.period_days)


def _fixture_hard(r: dict[str, Any]) -> tuple:
    return (r["field"], r["operator"], r["value"], r.get("currency"), r.get("scope"), r.get("period_days"))


# --- Expected unseen readings (oracle unseen_instructions) --------------------------------
UNSEEN_EXPECTED: dict[str, dict[str, Any]] = {
    TICKETS: {
        "rules": [
            ("cart.quantity", "=", 2, None, None, None),
            ("items[].unit_price_chf", "<=", 90, "CHF", "purchase", None),
            ("unverifiable", "=", "from the official ticket seller", None, None, None),
        ],
        "requested_item": "concert tickets",
        "questions": ["CHF 180", "official ticket seller"],
    },
    LUNCH: {
        "rules": [
            ("authorization.billing_amount_chf", "<", 25, "CHF", "purchase", None),
            ("items[].item_category", "in", ["dining", "food_delivery"], None, None, None),
            ("authorization.weekday", "in", WEEKDAYS, None, None, None),
        ],
        "requested_item": None,
        "questions": [],
    },
    PRESENT: {
        "rules": [
            ("unverifiable", "=", "the birthday present I picked", None, None, None),
            ("merchant.merchant_country", "=", "CH", None, None, None),
            ("authorization.delivery_by", "<=", "2026-08-14", None, None, None),
        ],
        "requested_item": None,
        "questions": ["No amount stated"],
    },
    GYM: {
        "rules": [
            ("items[].item_category", "in", ["membership"], None, None, None),
            ("authorization.billing_amount_chf", "=", 59, "CHF", "purchase", None),
            ("merchant.familiar_on_card", "=", "true", None, None, None),  # "renew": same gym, ask if not
        ],
        "requested_item": "gym membership",
        "questions": [],
    },
}


# The hand-built policies (the oracle's and the replay's input) name no item type for a
# chosen product; both compiler paths add the requested item's catalogue type (inferred).
ITEM_TYPE = {"SCEN0002": ["sporting_goods"], "SCEN0004": ["electronics"]}


def _compiled(policy: dict[str, Any]) -> tuple[set[tuple], dict[str, Any]]:
    """The fixture policy as the compiler reads its instruction: rules and flags."""
    rules = [_fixture_hard(r) for r in policy["rules"]]
    flags = dict(policy)
    if types := ITEM_TYPE.get(policy["scenario_id"]):
        rules.append(("items[].item_category", "in", types, None, None, None))
        flags["allowed_item_categories"] = types
    return _expected_set(rules), flags


def _as_set(rules: list[Rule]) -> set[tuple]:
    return {tuple(tuple(x) if isinstance(x, list) else x for x in _hard(r)) for r in rules}


def _expected_set(expected) -> set[tuple]:
    return {tuple(tuple(x) if isinstance(x, list) else x for x in e) for e in expected}


# --- A mocked model that reads the nine instructions well --------------------------------
def _r(field: str, op: str, words: str, *, num: float | None = None, text: str | None = None,
       lst: list[str] | None = None, value_from: str = "literal", currency: str | None = None,
       scope: str | None = None, period_days: int | None = None, source: str = "exact",
       on_fail: str = "decline") -> dict[str, Any]:
    return {"field": field, "operator": op, "value_number": num, "value_text": text, "value_list": lst,
            "value_from": value_from, "currency": currency, "scope": scope, "period_days": period_days,
            "words": words, "source": source, "on_fail": on_fail}


def _out(rules, *, item=None, extra=False, questions=(), uncertainty="ask") -> dict[str, Any]:
    return {"uncertainty_policy": uncertainty, "requested_item": item, "nothing_extra": extra,
            "rules": list(rules), "open_questions": list(questions)}


BILL = "authorization.billing_amount_chf"
MODEL_READINGS: dict[str, dict[str, Any]] = {
    PUBLIC[0]["instruction"]: _out([
        _r(BILL, "<=", "for CHF 20 or less", num=20, currency="CHF", scope="purchase"),
        _r("items[].item_category", "in", "grocery item", lst=["groceries"], source="inferred"),
        _r("merchant.familiar_on_card", "=", "from a shop I use regularly", text="true", source="inferred"),
    ]),
    PUBLIC[1]["instruction"]: _out([
        _r("items[].item_category", "in", "household groceries", lst=["groceries"], source="inferred"),
        _r(BILL, "<=", "each order at or below CHF 120 including delivery", num=120, currency="CHF",
           scope="purchase"),
        _r(BILL, "<=", "the total across any seven days at or below CHF 300", num=300, currency="CHF",
           scope="period", period_days=7),
    ]),
    PUBLIC[2]["instruction"]: _out([
        _r("items[].size_eu", "=", "in size 43", num=43),
        _r("merchant.merchant_category", "=", "specialist sports retailer", text="sporting_goods"),
        _r("order.return_window_days", ">=", "returned within 14 days or more", num=14),
        _r(BILL, "<=", "pay no more than CHF 200", num=200, currency="CHF", scope="purchase"),
    ], item="road-running shoes"),
    PUBLIC[3]["instruction"]: _out([
        _r("items[].item_category", "in", "clothing", lst=["clothing"]),
        _r(BILL, "<=", "up to CHF 250 per order", num=250, currency="CHF", scope="purchase"),
        _r("merchant.familiar_on_card", "=", "from shops I have used before", text="true"),
    ]),
    PUBLIC[4]["instruction"]: _out([
        _r("merchant.familiar_on_card", "=", "from a seller I have bought from before", text="true"),
        _r(BILL, "<=", "for CHF 400 or less", num=400, currency="CHF", scope="purchase"),
    ], item="27-inch monitor", extra=True),
    TICKETS: _out([
        _r("cart.quantity", "=", "two", num=2),
        _r("items[].unit_price_chf", "<=", "max CHF 90 each", num=90, currency="CHF", scope="purchase"),
        _r("unverifiable", "=", "from the official ticket seller", text="from the official ticket seller"),
    ], item="concert tickets", questions=[
        "No per-order limit stated: is the order limit CHF 180 (two tickets at CHF 90 each)?",
        "Which shop is the official ticket seller?"]),
    LUNCH: _out([
        _r(BILL, "<", "under CHF 25", num=25, currency="CHF", scope="purchase"),
        _r("items[].item_category", "in", "lunch", lst=["dining", "food_delivery"], source="inferred"),
        _r("authorization.weekday", "in", "on weekdays only", lst=WEEKDAYS),
    ]),
    PRESENT: _out([
        _r("unverifiable", "=", "the birthday present I picked", text="the birthday present I picked"),
        _r("merchant.merchant_country", "=", "from a Swiss shop", text="CH"),
        _r("authorization.delivery_by", "<=", "must arrive by Friday", text="fri", value_from="next_weekday"),
    ], questions=["No amount stated: what is the most this present may cost?"]),
    GYM: _out([
        _r("items[].item_category", "in", "gym membership", lst=["membership"], source="inferred"),
        _r(BILL, "=", "same price as last time", value_from="last_price", currency="CHF", scope="purchase",
           source="inferred", on_fail="ask"),
        _r("merchant.familiar_on_card", "=", "Renew", text="true", source="inferred", on_fail="ask"),
    ], item="gym membership"),
}


class ScriptedProvider:
    """Returns the canned reading for the instruction in the user message."""

    def __init__(self, readings: dict[str, dict[str, Any]]) -> None:
        self.readings = readings
        self.calls: list[tuple[str, float]] = []

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        self.calls.append((user, timeout_s))
        for instruction, reading in self.readings.items():
            if f"<<<\n{instruction}\n>>>" in user:
                return reading
        raise ProviderUnavailable("no scripted reading")


class FailingProvider:
    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        raise ProviderUnavailable("timed out")


# --- LLM path (provider mocked) ---------------------------------------------------------
@pytest.mark.parametrize("policy", PUBLIC, ids=[p["instruction"][:30] for p in PUBLIC])
def test_llm_compiles_public_instructions_to_the_fixture_rules(policy, history):
    provider = ScriptedProvider(MODEL_READINGS)
    draft = compile_instruction(policy["instruction"], history, CARD, provider)
    assert draft.compiler == "llm"
    assert draft.instruction == policy["instruction"]  # verbatim
    rules, flags = _compiled(policy)
    assert _as_set(draft.rules) == rules
    for field in ("requested_item", "requires_known_shop", "nothing_extra", "shop_type",
                  "allowed_item_categories", "uncertainty_policy"):
        assert getattr(draft, field) == flags[field], field
    assert provider.calls and provider.calls[0][1] == 8.0


@pytest.mark.parametrize("instruction", list(UNSEEN_EXPECTED), ids=["tickets", "lunch", "present", "gym"])
def test_llm_compiles_unseen_instructions_to_the_oracle_rules(instruction, history):
    expected = UNSEEN_EXPECTED[instruction]
    draft = compile_instruction(instruction, history, CARD, ScriptedProvider(MODEL_READINGS))
    assert draft.compiler == "llm"
    assert _as_set(draft.rules) == _expected_set(expected["rules"])
    assert draft.requested_item == expected["requested_item"]
    assert draft.uncertainty_policy == UNSEEN[instruction]["uncertainty_policy"]
    for fragment in expected["questions"]:
        assert any(fragment in q for q in draft.open_questions), (fragment, draft.open_questions)
    if not expected["questions"]:
        assert draft.open_questions == []


def test_same_price_comes_from_history_and_asks_when_it_changes(history):
    draft = compile_instruction(GYM, history, CARD, ScriptedProvider(MODEL_READINGS))
    rule = next(r for r in draft.rules if r.field == BILL)
    assert rule.value == 59.0 and rule.on_fail == "ask" and rule.source == "inferred"
    assert "Alpine Fitness Club" in rule.text and "ask me if it changed" in rule.text


# --- on_fail ask: one rule, the one before "ask me if anything changed" ------------------
def _gym_reading(ask_on: set[str]) -> dict[str, Any]:
    """The model's gym reading with on_fail ask on exactly the given fields."""
    reading = MODEL_READINGS[GYM]
    return reading | {"rules": [r | {"on_fail": "ask" if r["field"] in ask_on else "decline"}
                                for r in reading["rules"]]}


def test_the_parser_records_which_rule_the_ask_clause_covers(history):
    draft = parse(GYM, history, CARD)
    assert draft.asked_about == {"C1-same": "same price as last time, ask me if anything changed",
                                 "C9-same": "Renew … ask me if anything changed"}
    assert {r.id for r in draft.rules if r.on_fail == "ask"} == {"C1-same", "C9-same"}
    # The clause before the ask holds two rules: neither is covered, both decline.
    both = parse("Buy groceries up to CHF 50, ask me if anything changed")
    assert both.asked_about == {} and {r.on_fail for r in both.rules} == {"decline"}


def test_gym_with_ask_on_the_item_type_is_rejected_and_falls_back(history):
    reading = _gym_reading({"items[].item_category", BILL, "merchant.familiar_on_card"})
    floor = parse(GYM, history, CARD)
    read = read_with_llm(GYM, ScriptedProvider({GYM: reading}), history, CARD, date(2026, 8, 10))
    issues = lint(read, floor.asked_about).issues
    assert [(i.code, i.rule_id) for i in issues] == [("on_fail_not_stated", "C3-same")]
    assert "same price as last time" in issues[0].message

    draft = compile_instruction(GYM, history, CARD, ScriptedProvider({GYM: reading}))
    assert draft.compiler == "fallback"
    assert {r.id: r.on_fail for r in draft.rules} == {"C3": "decline", "C1-same": "ask", "C9-same": "ask"}


def test_gym_with_ask_on_the_price_rule_passes(history):
    floor = parse(GYM, history, CARD)
    read = read_with_llm(GYM, ScriptedProvider(MODEL_READINGS), history, CARD, date(2026, 8, 10))
    assert lint(read, floor.asked_about).ok
    draft = compile_instruction(GYM, history, CARD, ScriptedProvider(MODEL_READINGS))
    assert draft.compiler == "llm"
    assert {r.id: r.on_fail for r in draft.rules} == {"C3": "decline", "C1-same": "ask", "C9-same": "ask"}


def test_ask_on_a_rule_the_clause_does_not_cover_is_rejected(history):
    """No ask phrase: nothing is covered. Two rules from one clause asking: one too many."""
    plain = "Renew my gym membership, same price as last time"
    assert parse(plain, history, CARD).asked_about == {}
    read = read_with_llm(plain, ScriptedProvider({plain: _gym_reading({BILL})}), history, CARD)
    assert [i.rule_id for i in lint(read, {}).issues] == ["C1-same"]
    twice = read.model_copy(update={"instruction": GYM, "rules": read.rules + [
        read.rules[1].model_copy(update={"id": "C1-same-2", "value": 60})]})
    assert [i.rule_id for i in lint(twice, {"C1-same": "same price as last time"}).issues
            if i.code == "on_fail_not_stated"] == ["C1-same-2"]


def test_model_values_outside_the_vocabulary_become_questions(history):
    bad = _out([
        _r(BILL, "<=", "for CHF 20 or less", num=20, currency="CHF", scope="purchase"),
        _r("items[].item_category", "in", "fancy things", lst=["luxury"]),
        _r("merchant.merchant_country", "=", "from Narnia", text="NARNIA"),
    ])
    draft = compile_instruction("Buy fancy things from Narnia for CHF 20 or less",
                                history, CARD, ScriptedProvider({"Buy fancy things from Narnia for CHF 20 or less": bad}))
    assert all(r.field == BILL for r in draft.rules)
    assert sum("could not turn" in q for q in draft.open_questions) == 2


def test_an_invented_model_limit_loses_to_the_fallback(history):
    instruction = "Buy clothing for me, up to CHF 250 per order"
    invented = _out([_r(BILL, "<=", "up to CHF 250 per order", num=500, currency="CHF", scope="purchase")])
    draft = compile_instruction(instruction, history, CARD, ScriptedProvider({instruction: invented}))
    assert draft.compiler == "fallback"
    assert [r.value for r in draft.rules if r.field == BILL] == [250]


@pytest.mark.parametrize("provider", [NullProvider(), FailingProvider()], ids=["null", "timeout"])
def test_no_model_means_fallback(provider, history):
    draft = compile_instruction(PUBLIC[1]["instruction"], history, CARD, provider)
    assert draft.compiler == "fallback"
    assert {r.text for r in draft.rules} >= {"Total at or below CHF 120 per order",
                                             "Total at or below CHF 300 across any 7 days"}


# --- Fallback parser --------------------------------------------------------------------
@pytest.mark.parametrize("policy", PUBLIC, ids=[p["instruction"][:30] for p in PUBLIC])
def test_fallback_parses_public_instructions(policy, history):
    draft = parse(policy["instruction"], history, CARD)
    rules, flags = _compiled(policy)
    assert _as_set(draft.rules) == rules
    for field in ("requested_item", "requires_known_shop", "nothing_extra", "shop_type",
                  "allowed_item_categories", "uncertainty_policy"):
        assert getattr(draft, field) == flags[field], field
    # §3.9: limit checks use exactly the wording the UI parses.
    limits = {r["text"] for r in policy["rules"] if r["kind"] in ("amount", "period")}
    assert limits <= {r.text for r in draft.rules}
    assert lint(draft).ok


@pytest.mark.parametrize("instruction", list(UNSEEN_EXPECTED), ids=["tickets", "lunch", "present", "gym"])
def test_fallback_parses_unseen_instructions(instruction, history):
    expected = UNSEEN_EXPECTED[instruction]
    draft = parse(instruction, history, CARD)
    assert _as_set(draft.rules) == _expected_set(expected["rules"])
    assert draft.requested_item == expected["requested_item"]
    for fragment in expected["questions"]:
        assert any(fragment in q for q in draft.open_questions), (fragment, draft.open_questions)
    assert lint(draft).ok, lint(draft).issues


def test_boundary_words_are_kept():
    under = parse("Order lunch under CHF 25")
    at_most = parse("Order lunch for CHF 25 or less")
    assert [r.operator for r in under.rules if r.field == BILL] == ["<"]
    assert [r.operator for r in at_most.rules if r.field == BILL] == ["<="]
    assert "Total under CHF 25 per order" in {r.text for r in under.rules}


def test_foreign_currency_limit_is_shown_in_chf():
    draft = parse("Buy clothing up to EUR 250 per order")
    rule = next(r for r in draft.rules if r.field == BILL)
    assert (rule.value, rule.currency) == (250, "EUR")
    assert rule.text.startswith("Total at or below CHF 237.50 per order")
    assert lint(draft).ok


def test_decline_when_unsure_is_read():
    assert parse("Buy groceries up to CHF 50, decline if unsure").uncertainty_policy == "decline"
    assert parse("Buy groceries up to CHF 50").uncertainty_policy == "ask"


# --- Lint -------------------------------------------------------------------------------
def _draft(instruction: str, rules: list[Rule], questions: list[str] | None = None) -> ParsedDraft:
    return ParsedDraft(instruction=instruction, rules=rules, uncertainty_policy="ask",
                       open_questions=questions or [])


def _rule(id_: str, op: str, value: Any, *, field: str = BILL, scope: str | None = "purchase",
          source: str = "exact", text: str = "Total", currency: str | None = "CHF") -> Rule:
    return Rule(id=id_, field=field, operator=op, value=value, currency=currency, scope=scope,
                text=text, source=source)


def test_lint_rejects_a_vague_instruction_without_an_open_question():
    result = lint(_draft("buy something nice", []))
    assert not result.ok and [i.code for i in result.issues] == ["no_amount_cap"]


def test_vague_instruction_compiles_to_open_questions_not_limits(history):
    draft = compile_instruction("buy something nice", history, CARD, NullProvider())
    assert draft.rules == []
    assert any("No amount stated" in q for q in draft.open_questions)
    assert any("What should the agent buy" in q for q in draft.open_questions)


def test_lint_flags_invented_changed_and_contradictory_rules():
    text = "Buy lunch under CHF 25"
    assert [i.code for i in lint(_draft(text, [_rule("C1", "<=", 25)])).issues] == ["boundary_changed"]
    assert "invented_value" in [i.code for i in lint(_draft(text, [_rule("C1", "<", 25), _rule("C1-2", "<", 40)])).issues]
    contradiction = _draft("Buy lunch at CHF 30 exactly, under CHF 25", [_rule("C1", "=", 30), _rule("C1-2", "<", 25)])
    assert "contradiction" in [i.code for i in lint(contradiction).issues]
    categories = _draft("Buy lunch under CHF 25", [
        _rule("C1", "<", 25),
        _rule("C3", "in", ["dining"], field="items[].item_category", scope=None, currency=None),
        _rule("C4", "not_in", ["dining"], field="items[].item_category", scope=None, currency=None)])
    assert "contradiction" in [i.code for i in lint(categories).issues]


def test_lint_accepted_needs_the_cap_and_every_exact_check():
    rules = [_rule("C1", "<=", 120),
             _rule("C3", "in", ["groceries"], field="items[].item_category", scope=None, currency=None,
                   source="inferred")]
    assert lint_accepted(rules, ["C1", "C3"]).ok
    assert lint_accepted(rules, ["C1"]).ok  # an inferred check may be dropped
    assert lint_accepted(rules, ["C3"]).missing == ["per_order_limit", "C1"]


# --- Dry-run ----------------------------------------------------------------------------
def test_dry_run_replays_recent_history(history):
    draft = compile_instruction(PUBLIC[0]["instruction"], history, CARD, NullProvider())
    dry = draft.dry_run
    assert dry.sample_size == 5
    assert dry.would_fit + dry.would_violate + dry.would_ask == dry.sample_size
    assert dry.would_violate >= 3  # the gym, the gym again, the laptop are over CHF 20
    assert dry.examples and len(dry.examples) <= 3 and dry.examples[0].outcome == "violate"
    assert dry.agent_history is not None and (dry.agent_history.attempts, dry.agent_history.approved) == (1, 1)


def test_dry_run_without_history_says_so():
    draft = compile_instruction(LUNCH, StoreHistoryIndex(rows=[]), CARD, NullProvider())
    assert draft.dry_run.sample_size == 0 and draft.dry_run.examples is None
    assert "No purchases" in draft.dry_run.insight


def test_compile_instruction_is_registered():
    load_implementations()
    assert IMPLEMENTATIONS["compile_instruction"] is compile_instruction


def test_the_reference_date_is_the_simulated_present(history):
    draft = parse(PRESENT, history, CARD)
    rule = next(r for r in draft.rules if r.field == "authorization.delivery_by")
    assert rule.value == "2026-08-14" and "Fri 14 Aug 2026" in rule.text
    assert parse(PRESENT, today=date(2026, 8, 14)).rules  # a Friday reads as the next one
    assert next(r.value for r in parse(PRESENT, today=date(2026, 8, 14)).rules
                if r.field == "authorization.delivery_by") == "2026-08-21"


@pytest.mark.parametrize("provider", [NullProvider(), ScriptedProvider(MODEL_READINGS)], ids=["fallback", "llm"])
def test_by_friday_counts_from_a_given_confirmation_time(history, provider):
    """No route passes ``confirmed_at`` (C2 does not pass the real clock), so "by Friday"
    counts from the card's simulated present. A caller that gives one gets its
    Europe/Zurich date as "today": Thu 13 Aug 22:30 UTC is already Fri 14 Aug in Zurich,
    so "by Friday" is the next one, 21 Aug."""
    def delivery_by(**when: Any) -> Rule:
        draft = compile_instruction(PRESENT, history, CARD, provider, **when)
        return next(r for r in draft.rules if r.field == "authorization.delivery_by")

    assert delivery_by().value == "2026-08-14"  # no confirmation time: the card's simulated present
    assert delivery_by(confirmed_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC)).value == "2026-08-14"
    late = delivery_by(confirmed_at=datetime(2026, 8, 13, 22, 30, tzinfo=UTC))
    assert late.value == "2026-08-21" and "Fri 21 Aug 2026" in late.text
    assert delivery_by(confirmed_at=datetime(2026, 8, 13, 22, 30, tzinfo=UTC), today=date(2026, 8, 10)).value == "2026-08-14"
