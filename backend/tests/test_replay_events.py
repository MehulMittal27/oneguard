"""Replay harness: the data pack becomes 45 live-shaped, schema-valid events."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from oneguard.replay.events import (
    Pack,
    RunDecision,
    all_events,
    build_events,
    event_validator,
    parse_ts,
    recent_attempt_counts,
    with_run_context,
)
from oneguard.replay.runner import main as runner_main

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def pack() -> Pack:
    return Pack.load()


@pytest.fixture(scope="module")
def events(pack: Pack) -> dict[str, list[dict]]:
    return all_events(pack, now=NOW)


def test_every_attempt_becomes_one_event(pack, events):
    flat = [e for scenario in events.values() for e in scenario]
    assert len(flat) == len(pack.attempts) == 45
    assert {e["authorization"]["source_authorization_id"] for e in flat} == {
        r["authorization_id"] for r in pack.attempts
    }


def test_event_counts_match_the_catalogue(pack, events):
    for scenario_id, scenario in pack.scenarios.items():
        assert len(events[scenario_id]) == int(scenario["event_count"])


def test_every_event_validates_against_the_schema(events):
    validator = event_validator()
    for scenario in events.values():
        for event in scenario:
            errors = [f"{e.json_path}: {e.message}" for e in validator.iter_errors(event)]
            assert errors == [], event["authorization"]["source_authorization_id"]


def test_events_arrive_in_replay_order_with_monotonic_time(events):
    for scenario in events.values():
        orders = [e["authorization"]["replay_order"] for e in scenario]
        assert orders == list(range(1, len(scenario) + 1))
        times = [parse_ts(e["authorization"]["timestamp"]) for e in scenario]
        assert times == sorted(times)


def test_rebuilt_velocity_equals_the_csv_column(pack, events):
    csv_counts = {r["authorization_id"]: int(r["recent_attempt_count_10m"]) for r in pack.attempts}
    for scenario in events.values():
        for event in scenario:
            a = event["authorization"]
            assert a["recent_attempt_count_10m"] == csv_counts[a["source_authorization_id"]]


def test_velocity_window_boundaries():
    t = [parse_ts(s) for s in (
        "2026-08-18T02:00:00Z",
        "2026-08-18T02:10:00Z",  # exactly 10 min after the first: counts it
        "2026-08-18T02:10:00Z",  # same instant as the previous: does not count it
        "2026-08-18T02:20:01Z",  # 10 min 1 s after 02:10: counts neither
    )]
    assert recent_attempt_counts(t) == [0, 1, 1, 0]


def test_cart_lines_and_money_are_carried_through(pack, events):
    lines = sum(len(e["authorization"]["items"]) for s in events.values() for e in s)
    assert lines == sum(len(v) for v in pack.items.values()) == 56
    for scenario in events.values():
        for event in scenario:
            a = event["authorization"]
            assert [i["line_no"] for i in a["items"]] == list(range(1, len(a["items"]) + 1))
            # amount already includes delivery (rules.md M3)
            assert a["amount"] == pytest.approx(a["items_subtotal"] + a["delivery_fee"])


def test_missing_values_stay_null_not_zero(events):
    for scenario in events.values():
        for event in scenario:
            a = event["authorization"]
            assert a["spend_in_period_before_chf"] is None  # empty in this pack
            assert event["context"]["approved_spend_in_period_chf"] is None


def test_mandate_identity_is_the_scenario_authority(pack, events):
    for scenario_id, scenario in events.items():
        for event in scenario:
            a, m = event["authorization"], event["mandate"]
            assert m["card_id"] == a["card_id"]
            assert m["instruction"] == pack.scenarios[scenario_id]["cardholder_instruction"]


def test_real_clock_fields_come_from_now(events):
    event = events["SCEN0000"][0]
    assert event["runtime"]["received_at"] == "2026-09-25T12:00:00Z"
    assert event["deadline_at"] == "2026-09-25T12:00:08Z"


def test_live_ids_rewrite_related_ids_through_the_same_map(pack):
    events = build_events(pack, "SCEN0004", live_id=lambda s: f"LIVE-{s}", now=NOW)
    linked = [e for e in events if e["authorization"]["related_authorization_id"]]
    assert linked, "the pack has at least one re-quote"
    for event in linked:
        a = event["authorization"]
        assert a["authorization_id"].startswith("LIVE-")
        assert a["related_authorization_id"].startswith("LIVE-")
        assert not a["source_authorization_id"].startswith("LIVE-")


def test_run_context_keeps_only_the_history_window(pack):
    event = build_events(pack, "SCEN0001", now=NOW)[4]  # 6 min after the previous order
    at = parse_ts(event["authorization"]["timestamp"])
    decided = [
        RunDecision("A", "2026-08-13T17:20:00Z", "ME0001", 70.0, "approved"),
        RunDecision("B", "2026-08-12T10:05:00Z", "ME0001", 126.0, "declined"),
    ]
    enriched = with_run_context(event, decided)
    assert [r["authorization_id"] for r in enriched["context"]["recent_authorizations"]] == ["A"]
    assert all(parse_ts(r["timestamp"]) < at for r in enriched["context"]["recent_authorizations"])
    assert event["context"]["recent_authorizations"] == []  # input not mutated
    event_validator().validate(enriched)


def test_unknown_scenario_is_an_error(pack):
    with pytest.raises(KeyError):
        build_events(pack, "SCEN9999")


def test_runner_exits_clean_for_all_scenarios(capsys):
    assert runner_main(["--all"]) == 0
    assert "SCEN0004" in capsys.readouterr().out


def test_runner_decides_a_scenario_through_the_real_pipeline(capsys):
    """`make replay SCEN=SCEN0004`: every event decided and explained, declines with their counterfactual."""
    fixture = Path(__file__).parent / "fixtures" / "policies" / "SCEN0004.yaml"
    assert runner_main(["--scenario", "SCEN0004", "--policy", str(fixture)]) == 0
    table = capsys.readouterr().out.split("### Decisions", 1)[1]
    rows = [row.split(" | ") for row in table.splitlines() if row.startswith("| AU")]
    assert len(rows) == 11
    for source_id, outcome, message, counterfactual in rows:
        assert message.startswith(("Approved CHF", "Declined CHF", "Waiting for you CHF")), source_id
        assert "[instructions removed]" not in message + counterfactual, source_id
        if outcome == "decline":  # the counterfactual is its own column, never inside the message
            assert counterfactual.strip(" |").startswith("Would approve"), source_id
            assert "Would approve" not in message, source_id
