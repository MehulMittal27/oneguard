"""Offline replay runner: `make replay SCEN=SCEN0004` / `make replay-all`.

Builds the scenario's events, validates each against the event schema and prints them in
delivery order. With ``--policy`` (a policy YAML, e.g. the oracle fixture `make replay`
passes) it also decides every event through the real pipeline, ``decide_event`` with the
registered engine functions, on a fresh store: a seeded temp SQLite for history and a
``StoreLedger`` (with the session watch) for the run. It then prints what the customer
reads: outcome, message and counterfactual. Step-ups stay pending; no answer is invented
(CLAUDE.md rule 6).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import yaml
from sqlalchemy.orm import Session

from oneguard.engine.interfaces import load_implementations
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.types import Policy
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine
from oneguard.store.history import StoreHistoryIndex

from .events import Pack, build_events, event_validator

# What decide_event calls with no provider configured (tier 2 needs one; tier 3 runs after posting).
DECIDING = frozenset({
    "build_facts", "evaluate_rules", "protections", "warning_signs", "soft_signals", "decide", "explain",
})  # fmt: skip


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


def load_policy(path: Path, mandate_id: str) -> Policy:
    """A policy YAML (the oracle fixture shape) as the engine's Policy."""
    fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
    fixture.pop("scenario_id", None)
    return Policy.model_validate({"mandate_id": mandate_id, **fixture})


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|")


class Decided(NamedTuple):
    """One replayed purchase as the customer reads it."""

    source_id: str
    outcome: str
    reason_codes: list[str]
    message: str
    counterfactual: str


def decide_all(pack: Pack, scenario_id: str, policy: Policy, signals: bool) -> list[Decided]:
    """Every event of the scenario through the real pipeline, in delivery order."""
    implementations = load_implementations()
    missing = sorted(DECIDING - set(implementations))
    if missing:  # the real pipeline only: never a stand-in
        raise SystemExit(f"not every engine function is registered: {', '.join(missing)}")
    rows = []
    with tempfile.TemporaryDirectory(prefix="oneguard-replay-") as folder:
        engine = make_engine(f"sqlite:///{Path(folder) / 'replay.sqlite'}")
        seed_module.run(engine=engine)
        with Session(engine, expire_on_commit=False) as s:
            ctx = PipelineContext(
                policy=policy, ledger=StoreLedger(s, history=StoreHistoryIndex.load(s)),
                history=StoreHistoryIndex.load(s), run_id=f"replay-{scenario_id}",
                signals_enabled=signals, implementations=implementations,
            )  # fmt: skip
            now = datetime.now(UTC)
            for event in build_events(pack, scenario_id, mandate_id=policy.mandate_id, now=now):
                decision, explanation, _ = decide_event(event, ctx)
                source = event["authorization"]["source_authorization_id"]
                rows.append(Decided(
                    source, decision.outcome, list(decision.reason_codes), explanation.message,
                    explanation.counterfactual or "",
                ))  # fmt: skip
        engine.dispose()
    return rows


def _decisions_table(rows: list[Decided]) -> str:
    lines = ["| id | outcome | message | counterfactual |", "|---|---|---|---|"]
    lines += [f"| {r.source_id} | {r.outcome} | {_cell(r.message)} | {_cell(r.counterfactual)} |" for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the data pack's purchase attempts.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", help="scenario id, e.g. SCEN0004")
    group.add_argument("--all", action="store_true", help="every scenario in the pack")
    parser.add_argument("--policy", type=Path, help="policy YAML: decide every event with it (--scenario only)")
    parser.add_argument("--signals", action="store_true", help="soft signals on (ONEGUARD_SOFT_SIGNALS)")
    args = parser.parse_args(argv)
    if args.policy and args.all:
        parser.error("--policy needs --scenario")

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
        if args.policy and not invalid:
            policy = load_policy(args.policy, mandate_id=f"TM_REPLAY_{scenario_id}")
            rows = decide_all(pack, scenario_id, policy, args.signals)
            print(f"\n### Decisions ({args.policy.name}, signals {'on' if args.signals else 'off'})\n")
            print(_decisions_table(rows))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
