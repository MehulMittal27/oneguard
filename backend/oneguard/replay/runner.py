"""Offline replay runner: `make replay SCEN=SCEN0004` / `make replay-all`.

Until the pipeline lands (Gate 0) this builds the events, validates each against the
event schema and prints them in delivery order. Deciding them is the pipeline's job and
is added here once `oneguard.pipeline.decide_event` exists.
"""

from __future__ import annotations

import argparse
import sys

from .events import Pack, build_events, event_validator


def _table(events: list[dict]) -> str:
    lines = [
        "| # | id | time (sim) | merchant | CHF | lines | 10m | related |",
        "|---:|---|---|---|---:|---:|---:|---|",
    ]
    for event in events:
        a = event["authorization"]
        lines.append(
            f"| {a['replay_order']} | {a['authorization_id']} | {a['timestamp']} "
            f"| {a['merchant']['merchant_id']} | {a['billing_amount_chf']:.2f} "
            f"| {len(a['items'])} | {a['recent_attempt_count_10m']} "
            f"| {a['related_authorization_id'] or ''} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the data pack's purchase attempts.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", help="scenario id, e.g. SCEN0004")
    group.add_argument("--all", action="store_true", help="every scenario in the pack")
    args = parser.parse_args(argv)

    pack = Pack.load()
    validator = event_validator()
    scenario_ids = pack.scenario_ids() if args.all else [args.scenario]

    invalid = 0
    for scenario_id in scenario_ids:
        events = build_events(pack, scenario_id)
        for event in events:
            for error in validator.iter_errors(event):
                invalid += 1
                source = event["authorization"]["source_authorization_id"]
                print(f"INVALID {source}: {error.json_path}: {error.message}", file=sys.stderr)
        print(f"\n## {scenario_id} — {len(events)} events\n")
        print(_table(events))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
