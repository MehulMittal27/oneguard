"""Warning signs W1–W6 on synthetic Facts / LedgerView: each fires, and not, at its boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from oneguard.engine import warnings as W
from oneguard.engine.facts import build_facts
from oneguard.engine.types import Signal
from tests.test_protections import facts, limit_rule, line, policy, view

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "scenario_fixtures" / "example_authorization_request.json"


def signal(signals: list[Signal], sid: str) -> Signal:
    [found] = [s for s in signals if s.id == sid]
    return found


def run(f=None, v=None, limit=None) -> list[Signal]:
    return W.evaluate(f or facts(), v or view(), limit)


def test_every_sign_is_reported_once_with_its_strength():
    signals = run()
    assert [s.id for s in signals] == ["W1", "W2", "W3", "W4", "W5", "W6"]
    assert [s.strength for s in signals] == ["strong", "strong", "weak", "weak", "weak", "weak"]
    assert all(s.outcome_if_triggered == "ask" for s in signals)
    assert not any(s.triggered for s in signals), "an ordinary purchase triggers nothing (P6)"


def test_w1_new_device():
    assert signal(run(facts(device_id="DVC-NEW")), "W1").triggered
    assert not signal(run(facts(device_id="DVC-1")), "W1").triggered
    assert signal(run(facts(device_id="")), "W1").triggered, "a missing device id is not a known one"


@pytest.mark.parametrize(("count", "fires"), [(0, False), (1, False), (2, True), (5, True)])
def test_w2_burst_from_two_earlier_attempts(count, fires):
    assert signal(run(facts(recent_attempt_count_10m=count)), "W2").triggered is fires


def test_w3_new_country():
    assert signal(run(facts(merchant_country="GB")), "W3").triggered
    assert not signal(run(facts(merchant_country="CH")), "W3").triggered


@pytest.mark.parametrize(("amount", "fires"), [(500.0, False), (500.01, True)])
def test_w4_strictly_above_the_largest_approved_purchase(amount, fires):
    f = facts(amount=amount, items=[line(price=amount)])
    assert signal(run(f), "W4").triggered is fires


def test_w4_never_fires_within_a_stated_per_order_limit():
    f = facts(amount=600.0, items=[line(price=600.0)])
    assert signal(run(f), "W4").triggered
    assert not signal(run(f, limit=600.0), "W4").triggered  # equal is within
    assert signal(run(f, limit=599.99), "W4").triggered


def test_w4_without_any_approved_purchase_is_a_weak_sign():
    v = view()
    v.max_approved_chf = None
    assert signal(run(v=v), "W4").triggered


@pytest.mark.parametrize(("hour", "fires"), [(23, False), (0, True), (4, True), (5, False)])
def test_w5_night_is_zero_to_five_local(hour, fires):
    assert signal(run(facts(local_hour=hour)), "W5").triggered is fires


@pytest.mark.parametrize(
    ("utc", "fires"),
    [
        ("2026-08-12T21:59:59Z", False),  # 23:59:59 in Zurich (CEST, UTC+2)
        ("2026-08-12T22:00:00Z", True),   # 00:00 in Zurich
        ("2026-08-13T02:59:59Z", True),   # 04:59:59 in Zurich
        ("2026-08-13T03:00:00Z", False),  # 05:00 in Zurich
        ("2026-12-12T23:30:00Z", True),   # 00:30 in Zurich in winter (CET, UTC+1)
    ],
)  # fmt: skip
def test_w5_uses_europe_zurich_time_from_real_facts(utc, fires):
    event = copy.deepcopy(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    event["authorization"]["timestamp"] = utc
    assert signal(W.evaluate(build_facts(event, None), view()), "W5").triggered is fires


def _priced(price, low=90.0, high=110.0):
    item = line(price=price).model_copy(update={"unit_price_min_chf": low, "unit_price_max_chf": high})
    return facts(amount=price, items=[item])


@pytest.mark.parametrize(("price", "fires"), [(89.99, True), (90.0, False), (110.0, False), (110.01, True)])
def test_w6_outside_catalogue_range(price, fires):
    w6 = signal(run(_priced(price)), "W6")
    assert w6.triggered is fires
    if fires:
        assert f"{price:.2f}" in w6.detail


def test_w6_unknown_range_is_not_a_sign():
    assert not signal(run(_priced(500.0, low=None, high=None)), "W6").triggered


def test_registered_warning_signs_is_the_interface_function():
    from oneguard.engine.interfaces import load_implementations

    assert load_implementations()["warning_signs"] is W.warning_signs


def test_registered_warning_signs_takes_the_per_order_limit_from_the_policy():
    f = facts(amount=600.0, items=[line(price=600.0)])
    assert signal(W.warning_signs(f, view(), policy()), "W4").triggered
    assert not signal(W.warning_signs(f, view(), policy([limit_rule(600)])), "W4").triggered
    assert signal(W.warning_signs(f, view(), policy([limit_rule(599.99)])), "W4").triggered


@pytest.mark.parametrize(("code", "shown"), [("DE", "Germany"), ("ch", "Switzerland"), ("gb", "the United Kingdom"),
                                             ("zz", "ZZ")])  # fmt: skip
def test_w3_names_the_country_never_a_lowercase_code(code, shown):
    w3 = signal(run(facts(merchant_country=code)), "W3")
    assert w3.detail.endswith(f"in {shown}.") or w3.detail.endswith(f"in {shown} before.")
    assert f" {code.lower()}." not in w3.detail
