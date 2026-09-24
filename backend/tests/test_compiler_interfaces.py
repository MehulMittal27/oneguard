"""Lint and dry-run through engine/interfaces.py (lane P4, issue #17).

``lint_accepted_ids`` and ``dry_run_policy`` are what the ``lint_accepted`` / ``dry_run``
interfaces resolve to once the ``contract:`` PR lists them; before that they are plain
functions and the registration test is skipped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from oneguard.compiler import dry_run_policy, lint_accepted_ids
from oneguard.engine.interfaces import IMPLEMENTATIONS, INTERFACES, load_implementations
from oneguard.engine.types import HistoryRow, Policy, Rule
from oneguard.store.history import StoreHistoryIndex

CARD = "CA_T1"
CAP = Rule(id="C1", field="authorization.billing_amount_chf", operator="<=", value=120, currency="CHF",
           scope="purchase", text="Total at or below CHF 120 per order", source="exact", kind="amount")
PERIOD = Rule(id="C2", field="authorization.billing_amount_chf", operator="<=", value=300, currency="CHF",
              scope="period", period_days=7, text="Total at or below CHF 300 across any 7 days", source="exact",
              kind="period")
GROCERIES = Rule(id="C3", field="items[].item_category", operator="in", value=["groceries"],
                 text="Only groceries", source="inferred", kind="item")


def test_lint_passes_when_the_cap_and_every_exact_check_are_kept():
    assert lint_accepted_ids([CAP, PERIOD, GROCERIES], ["C1", "C2"]) == ([], [])


def test_lint_names_the_missing_cap_first_then_each_dropped_exact_check():
    missing, reasons = lint_accepted_ids([CAP, PERIOD, GROCERIES], ["C3"])
    assert missing == ["per_order_limit", "C1", "C2"]
    assert reasons[0] == "the policy needs a limit on what one purchase may cost"
    assert reasons[1] == 'you stated "Total at or below CHF 120 per order" and it was left out'
    assert len(reasons) == len(missing)


def test_a_period_limit_is_not_a_per_order_cap():
    assert lint_accepted_ids([PERIOD], ["C2"])[0] == ["per_order_limit"]


def test_only_an_upper_bound_is_a_per_order_cap():
    def amount(operator: str) -> Rule:
        return Rule(id="C1", field="authorization.billing_amount_chf", operator=operator, value=20, currency="CHF",
                    scope="purchase", text=f"Total {operator} CHF 20", source="inferred", kind="amount")

    for floor in (">=", ">"):
        assert lint_accepted_ids([amount(floor)], ["C1"])[0] == ["per_order_limit"]
    for cap in ("<", "<=", "="):  # "=" is an exact price, e.g. the gym's "same price as last time"
        assert lint_accepted_ids([amount(cap)], ["C1"]) == ([], [])


def _row(n: int, day: int, chf: float, merchant: str) -> HistoryRow:
    return HistoryRow(
        authorization_id=f"TR_T{n:03d}", customer_id="CU_T1", card_id=CARD, initiator_type="human",
        timestamp=datetime(2026, 7, day, 12, tzinfo=UTC), transaction_type="purchase", status="approved",
        amount=chf, currency="CHF", billing_amount_chf=chf, merchant_id=merchant, merchant_name=merchant,
        merchant_category="groceries", merchant_country="CH", channel="ecommerce", recurring=False,
        customer_device_id="DVC_T1", description="Groceries")


def test_dry_run_of_a_form_policy_reads_the_known_shop_alias():
    history = StoreHistoryIndex(rows=[_row(1, 1, 40.0, "ME_A"), _row(2, 5, 150.0, "ME_A"), _row(3, 9, 30.0, "ME_B")])
    known_shop = Rule(id="known_shop", field="merchant.known_shop", operator="=", value="true",
                      text="Only from shops you have bought from before", source="exact", kind="merchant")
    policy = Policy(mandate_id="M_T", status="active", instruction="form", rules=[CAP, known_shop],
                    uncertainty_policy="ask", requires_known_shop=True)
    result = dry_run_policy(policy, history, CARD)
    assert result.sample_size == 3
    assert result.would_fit + result.would_violate + result.would_ask == 3
    assert result.would_violate == 1  # CHF 150 over CHF 120
    assert result.examples and result.examples[0].outcome == "violate"


@pytest.mark.skipif("lint_accepted" not in INTERFACES, reason="waiting for the contract: PR")
def test_the_interfaces_resolve_to_the_compiler():
    load_implementations()
    assert IMPLEMENTATIONS["lint_accepted"] is lint_accepted_ids
    assert IMPLEMENTATIONS["dry_run"] is dry_run_policy
