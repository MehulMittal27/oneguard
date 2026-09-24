"""R6: the four unseen instructions compile to P2's answer key (lane P4).

P2's expected policies live in tests/test_engine_core_unseen.py (``POLICIES``) and are
imported here, so the two lanes cannot drift apart. Both compiler paths are checked:
the English parser alone (no model) and the LLM path with a scripted provider.

Compared: every rule's id, field, operator, value, currency, scope, period_days and
on_fail; and the policy's requested_item, uncertainty_policy, requires_known_shop,
nothing_extra and shop_type. Not compared: rule ``text`` / ``kind`` (the compiler writes
the customer-facing wording) and the convenience ``allowed_item_categories``, which the
compiler restates from the C3 rule (types.Policy) and P2's key leaves unset. Ids of
``unverifiable`` rules are compared by content: P2 numbers them U1/U2 across policies,
the compiler per policy.

"By Friday" is the Friday after confirmation: the policy is confirmed on Mon 10 Aug 2026
(P2's PM decision), passed as ``today``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from oneguard.compiler import compile_instruction
from oneguard.compiler.parser import parse
from oneguard.engine.types import HistoryRow, Policy, Rule
from oneguard.llm.provider import NullProvider
from oneguard.store.history import StoreHistoryIndex
from tests.test_compiler import MODEL_READINGS, ScriptedProvider
from tests.test_engine_core_unseen import POLICIES

CARD = "CA_T1"
CONFIRMED = date(2026, 8, 10)  # Mon 10 Aug 2026
POLICY_FIELDS = ("requested_item", "uncertainty_policy", "requires_known_shop", "nothing_extra", "shop_type")


@pytest.fixture(scope="module")
def history() -> StoreHistoryIndex:
    """The customer's gym: last approved CHF 59.00 on 12 Jul (P2's answer key)."""
    def row(n: int, when: datetime, chf: float) -> HistoryRow:
        return HistoryRow(
            authorization_id=f"TR_T{n:03d}", customer_id="CU_T1", card_id=CARD, initiator_type="human",
            timestamp=when, transaction_type="purchase", status="approved", amount=chf, currency="CHF",
            billing_amount_chf=chf, merchant_id="ME_T_GYM1", merchant_name="Alpine Fitness Club",
            merchant_category="health", merchant_country="CH", channel="ecommerce", recurring=True,
            customer_device_id="DVC_T1", description="Monthly gym membership")

    return StoreHistoryIndex(rows=[row(1, datetime(2026, 6, 12, 9, tzinfo=UTC), 55.00),
                                   row(2, datetime(2026, 7, 12, 9, tzinfo=UTC), 59.00)])


def _rule_key(rule: Rule) -> tuple:
    value = rule.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    rule_id = "U" if rule.field == "unverifiable" else rule.id
    return (rule_id, rule.field, rule.operator, tuple(value) if isinstance(value, list) else value,
            rule.currency, rule.scope, rule.period_days, rule.on_fail)


def _assert_equal(compiled, expected: Policy) -> None:
    assert sorted(map(_rule_key, compiled.rules)) == sorted(map(_rule_key, expected.rules))
    for name in POLICY_FIELDS:
        assert getattr(compiled, name) == getattr(expected, name), name


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_the_parser_compiles_the_unseen_instruction_to_p2s_policy(name, history):
    expected = POLICIES[name]
    _assert_equal(parse(expected.instruction, history, CARD, today=CONFIRMED), expected)


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_compile_instruction_gives_p2s_policy_with_and_without_the_model(name, history):
    expected = POLICIES[name]
    for provider, compiler in ((NullProvider(), "fallback"), (ScriptedProvider(MODEL_READINGS), "llm")):
        draft = compile_instruction(expected.instruction, history, CARD, provider, today=CONFIRMED)
        assert draft.compiler == compiler
        _assert_equal(draft, expected)


def test_on_fail_ask_is_only_where_the_customer_said_so(history):
    for name, expected in POLICIES.items():
        draft = parse(expected.instruction, history, CARD, today=CONFIRMED)
        asking = {r.id for r in draft.rules if r.on_fail == "ask"}
        assert asking == ({"C1-same", "C9-same"} if name == "gym" else set()), name
