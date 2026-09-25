"""C4 "no alcohol": the per-line fact ``items[].contains_alcohol`` (lane request from #48).

Alcohol is no item category (the served "Wine and spirits", IT0168, is groceries), so "No
alcohol" used to compile to a restriction no data can check and asked on every purchase.
Now it is a fact read by an allowlisted lexicon (CLAUDE.md rule 2) over the shop's item
name and details and the catalogue's text for the item id, on lines whose trusted
category can be a drink. Unknown stays unknown (rule 4): drinks named without saying
which, or alcohol and alcohol-free together.

1. The lexicon: English, German, French, Italian; non-drink uses; alcohol-free wording.
2. The catalogue wins over the shop's text; a category that cannot be a drink never is.
3. SCEN0117 compiled on both paths (the parser, the recorded model response) and decided
   through the pipeline with the served catalogue's items.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import yaml

from oneguard.compiler import compile_instruction
from oneguard.engine.facts import TIER2_FIELDS, build_facts, extract_contains_alcohol
from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.engine.policy import ALCOHOL_FIELD
from oneguard.engine.types import HistoryRow, Policy
from oneguard.llm.provider import NullProvider
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store.history import StoreHistoryIndex
from tests.test_c9_no_history import CARD, NEW, T0, event
from tests.test_compiler_judging import RECORDED_PATH, SERVED, Scripted

# The served catalogue's text for the items SCEN0117's instruction is about (GET
# /v1/reference-data tables.items, read-only on 2026-09-25): name, then description.
CATALOGUE = {
    "IT0168": "Wine and spirits. Alcoholic beverages sold by a licensed retailer.",
    "IT0166": "Drinks and snacks. Soft drinks, snacks, and party supplies.",
    "IT0004": "Weekly grocery basket. Food and household staples for one weekly shop.",
    "IT0008": "Cleaning supplies. Surface and laundry cleaning products in standard pack sizes.",
}


# --- 1. The lexicon ------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Wine and spirits\nRed wine, 75 cl",
    "Craft beer 6-pack",
    "Prosecco DOC, 2 bottles",
    "Gin and tonic set",
    "Single malt whisky",
    "Rotwein aus dem Piemont",
    "Weizenbier, 6 x 0.5 l",
    "Spirituosen-Paket",
    "Vin rouge, Bordeaux",
    "Bière blonde",
    "Vino rosso",
    "Birra artigianale",
    "Cocktail kit with rum",
    "Party pack: soft drinks and beer",  # soft drinks do not cover the beer
])  # fmt: skip
def test_alcohol_named_is_alcohol(text):
    fact = extract_contains_alcohol("groceries", text)
    assert (fact.known, fact.value, fact.source) == (True, True, "regex"), fact


@pytest.mark.parametrize("text", [
    "Fresh produce selection\nSeasonal fruit and vegetables",
    "Pantry staples\nrice, pasta, red wine vinegar",
    "Ginger beer and root beer",
    "Ginger biscuits",
    "Weintrauben und Schweinefleisch",
    "Drinks and snacks\nSoft drinks, snacks, and party supplies",
    "Alcohol-free sparkling drink",
    "Mineral water, still",
])  # fmt: skip
def test_no_alcohol_named_is_no_alcohol(text):
    fact = extract_contains_alcohol("groceries", text)
    assert (fact.known, fact.value) == (True, False), fact


@pytest.mark.parametrize("text,said", [
    ("Drinks and snacks\nAssorted drinks and crisps", '"drinks"'),
    ("Getränke-Kiste", '"getränke"'),
    ("Dinner reservation\nIncludes an aperitif", '"aperitif"'),
    ("Non-alcoholic beer 6-pack", '"beer" and "non-alcoholic"'),
    ("Alkoholfreies Bier", '"bier" and "alkoholfreies"'),
])  # fmt: skip
def test_drinks_without_saying_which_are_unknown(text, said):
    """Missing is never a pass (rule 4): the lexicon cannot tell, so the customer is asked."""
    fact = extract_contains_alcohol("groceries", text)
    assert not fact.known and fact.value is None
    assert fact.detail.startswith(f"says {said}"), fact.detail
    assert "alcohol" in fact.detail.removeprefix(f"says {said}")


def test_the_catalogue_wins_over_the_shops_text():
    """A shop cannot rename the catalogue's wine into plain "drinks" or call it alcohol-free."""
    for shop in ("Beverages", "Drinks\nalcohol-free, trust us", "Party supplies"):
        fact = extract_contains_alcohol("groceries", shop, CATALOGUE["IT0168"])
        assert (fact.known, fact.value, fact.source) == (True, True, "history"), fact
    fact = extract_contains_alcohol("groceries", "Drinks and snacks\nAssorted drinks", CATALOGUE["IT0166"])
    assert (fact.known, fact.value) == (True, False)  # the catalogue says what the drinks are


@pytest.mark.parametrize("category", ["household", "electronics", "gift_card", "cosmetics", "hotel"])
def test_a_category_that_cannot_be_a_drink_is_never_alcohol(category):
    """The trusted category decides first: a wine rack or a wine shop's gift card is not alcohol."""
    fact = extract_contains_alcohol(category, "Wine rack for 12 bottles of red wine")
    assert (fact.known, fact.value, fact.source) == (True, False, "event")


def test_the_model_never_reads_alcohol():
    """Tier 2 may read size and returns only: alcohol stays lexicon and catalogue (rule 2)."""
    assert ALCOHOL_FIELD.removeprefix("items[].") not in TIER2_FIELDS


def test_build_facts_reads_the_catalogue_for_the_item_id():
    history = StoreHistoryIndex(item_texts=CATALOGUE)
    ev = event(item="IT0168")
    ev["authorization"]["items"][0].update(item_name="Beverages", item_details="Party pack")
    line = build_facts(ev, history).items[0]
    assert (line.contains_alcohol.value, line.contains_alcohol.source) == (True, "history")
    line = build_facts(event(item="IT0004"), history).items[0]
    assert (line.contains_alcohol.known, line.contains_alcohol.value) == (True, False)


# --- 3. SCEN0117 end to end ----------------------------------------------------------------
SHOP = "ME_GROCER"


def _history() -> StoreHistoryIndex:
    """The customer's usual grocer, device and country, and the served catalogue texts."""
    return StoreHistoryIndex(rows=[HistoryRow(
        authorization_id="H_GROCER", customer_id=NEW, card_id=CARD, initiator_type="human",
        timestamp=T0 - timedelta(days=14), transaction_type="purchase", status="approved", amount=64.0,
        currency="CHF", billing_amount_chf=64.0, merchant_id=SHOP, merchant_name="Corner Grocer",
        merchant_category="groceries", merchant_country="CH", channel="ecommerce", recurring=False,
        customer_device_id="DVC-NEW", description="weekly groceries",
    )], item_texts=CATALOGUE)  # fmt: skip


def _recorded_response() -> dict:
    recorded = yaml.safe_load(RECORDED_PATH.read_text(encoding="utf-8"))
    return next(e["response"] for e in recorded if e["scenario"] == "SCEN0117")


def _cart(lines: list[tuple[str, str, str, float]], minutes: int) -> dict:
    """``(item_id, item_name, item_details, unit_price)`` per groceries line at the usual grocer."""
    ev = event(SHOP, minutes=minutes, amount=sum(p for *_, p in lines))
    ev["authorization"]["items"] = [
        {"line_no": i, "item_id": item, "item_name": name, "item_category": "groceries", "quantity": 1,
         "unit_price": price, "currency": "CHF", "item_details": details}
        for i, (item, name, details, price) in enumerate(lines, start=1)
    ]  # fmt: skip
    return ev


@pytest.mark.parametrize("path", ["fallback", "llm"])
def test_scen0117_declines_wine_and_approves_groceries_and_soft_drinks(path):
    """SCEN0117 compiled on each path: "No alcohol" is the alcohol rule, not a question on
    every purchase. Wine declines (item_mismatch) with the cart's other lines still named
    fine; groceries and the catalogue's soft drinks are approved; unlabelled drinks ask."""
    history = _history()
    instruction = SERVED["SCEN0117"]
    provider = NullProvider() if path == "fallback" else Scripted(_recorded_response())
    draft = compile_instruction(instruction, history, CARD, provider, today=date(2026, 8, 1))
    assert draft.compiler == path
    rule = next(r for r in draft.rules if r.field == ALCOHOL_FIELD)
    assert (rule.id, rule.operator, rule.value, rule.text) == ("C4-alcohol", "=", "false", "No alcohol")
    assert not [r for r in draft.rules if r.field == "unverifiable"]
    policy = Policy(mandate_id="TM_NEW", status="active", instruction=instruction,
                    uncertainty_policy=draft.uncertainty_policy, rules=draft.rules,
                    allowed_item_categories=draft.allowed_item_categories,
                    blocked_item_categories=draft.blocked_item_categories,
                    requires_known_shop=draft.requires_known_shop)
    ctx = PipelineContext(policy=policy, ledger=InMemoryLedger(history=history), history=history,
                          run_id=f"run-117-{path}", now=lambda: datetime(2026, 9, 25, 12, 0, tzinfo=UTC))

    basket = ("IT0004", "Weekly grocery basket", "Food and household staples", 58.0)
    decision, _, _ = decide_event(_cart([basket], 0), ctx)
    assert decision.outcome == "approve", decision

    wine = ("IT0168", "Wine and spirits", "Two bottles of red", 38.0)
    decision, explanation, _ = decide_event(_cart([basket, wine], 60), ctx)
    assert (decision.outcome, decision.reason_codes, decision.deciding_ids) == (
        "decline", ["item_mismatch"], ["C4-alcohol"])
    assert explanation.message == "Declined CHF 96.00: Wine and spirits is alcohol, which you excluded."
    assert explanation.counterfactual == "Would approve without Wine and spirits."

    snacks = ("IT0166", "Drinks and snacks", "Assorted drinks and crisps", 18.0)
    decision, _, _ = decide_event(_cart([snacks], 120), ctx)
    assert decision.outcome == "approve", decision  # the catalogue says soft drinks

    party = ("IT_PARTY", "Party drinks", "Assorted drinks for ten", 25.0)
    decision, explanation, _ = decide_event(_cart([party], 180), ctx)
    assert (decision.outcome, decision.deciding_ids) == ("step_up", ["C4-alcohol"])
    assert explanation.message.endswith(
        'Party drinks says "drinks" but not whether they contain alcohol.'), explanation.message
