"""Offline replay runner: `make replay SCEN=SCEN0004` / `make replay-all`.

Builds the scenario's events, validates each against the event schema and prints them in
delivery order. With ``--policy`` (a policy YAML, e.g. the oracle fixture `make replay`
passes) it also replays them as D2 does: ``api.offline.OfflineRunner`` at full speed,
``decide_event`` with every registered engine function, and prints what the customer
reads: outcome, message and counterfactual.

The replay is recorded like a live run: its ``runs`` row (``kind = replay``) is written
before the first decision, each event lands in ``events_raw`` and each decision in
``decisions``. Remembered answers and the session watch therefore stay inside the run and
never come from, or go to, a live one (docs/decisions.md M18). The store is a throwaway
seeded SQLite unless ``--database-url`` names one to keep the record in; there every
replay gets fresh live ids, as the platform assigns them per run. Step-ups stay pending;
no answer is invented (CLAUDE.md rule 6).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from sqlalchemy import Engine, select

from oneguard.api.offline import OfflineRunner, live_ids
from oneguard.engine.interfaces import load_implementations
from oneguard.engine.types import HistoryIndex, Policy
from oneguard.store import seed as seed_module
from oneguard.store.db import init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import Decision, Run

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


class Replay(NamedTuple):
    """One recorded replay: its ``runs`` row id and its purchases in delivery order."""

    run_id: str
    decided: list[Decided]


def open_store(url: str) -> Engine:
    """The store at ``url``, its tables created and, when it has none, its reference data seeded."""
    db = make_engine(url)
    init_db(db)
    with session(db) as s:
        empty = seed_module.history_row_count(s) == 0
    if empty:
        seed_module.run(engine=db)
    return db


def decide_all(pack: Pack, scenario_id: str, policy: Policy, signals: bool, *, db: Engine | None = None) -> Replay:
    """Replay every event of the scenario through the real pipeline and record the run.

    On ``db`` when given (seeded; fresh live ids), else on a throwaway seeded SQLite where
    the pack's ids are the live ids.
    """
    implementations = load_implementations()
    missing = sorted(DECIDING - set(implementations))
    if missing:  # the real pipeline only: never a stand-in
        raise SystemExit(f"not every engine function is registered: {', '.join(missing)}")
    if db is not None:
        return _replay(pack, scenario_id, policy, signals, db, implementations, fresh_ids=True)
    with tempfile.TemporaryDirectory(prefix="oneguard-replay-") as folder:
        temp = make_engine(f"sqlite:///{Path(folder) / 'replay.sqlite'}")
        try:
            seed_module.run(engine=temp)
            return _replay(pack, scenario_id, policy, signals, temp, implementations, fresh_ids=False)
        finally:
            temp.dispose()


def _replay(
    pack: Pack, scenario_id: str, policy: Policy, signals: bool, db: Engine,
    implementations: Mapping[str, Callable[..., Any]], *, fresh_ids: bool,
) -> Replay:  # fmt: skip
    attempts = pack.attempts_for(scenario_id)
    sources = [a["authorization_id"] for a in attempts]
    ids = live_ids(sources) if fresh_ids else {source: source for source in sources}
    events = build_events(pack, scenario_id, mandate_id=policy.mandate_id, live_id=ids.__getitem__)
    authority = pack.authorities[attempts[0]["authority_id"]]
    with session(db) as s:
        history = StoreHistoryIndex.load(s)
    run_id = asyncio.run(_offline(db, scenario_id, authority, policy, events, history, signals, implementations))
    with session(db) as s:
        run = s.get(Run, run_id)
        if run is None or run.state != "done":
            raise RuntimeError(f"replay {run_id} of {scenario_id} did not finish: {run.last_error if run else 'no runs row'}")
        rows = {d.live_authorization_id: d for d in s.scalars(select(Decision).where(Decision.run_id == run_id))}
        decided = [
            Decided(source, rows[live].outcome, list(rows[live].reason_codes), rows[live].message,
                    rows[live].counterfactual or "")
            for source, live in ids.items()
        ]  # fmt: skip
    return Replay(run_id, decided)


async def _offline(
    db: Engine, scenario_id: str, authority: dict[str, str], policy: Policy, events: list[dict[str, Any]],
    history: HistoryIndex, signals: bool, implementations: Mapping[str, Callable[..., Any]],
) -> str:  # fmt: skip
    """D2 at full speed on ``db``: the run id once the last event is decided."""
    offline = OfflineRunner(db, implementations=implementations)
    try:
        await offline.restart(
            scenario_id=scenario_id, card_id=authority["card_id"], customer_id=authority["customer_id"],
            policy=policy, events=events, history=history, provider=None, signals_enabled=signals, speed_ms=0,
        )  # fmt: skip
        await offline.finished()
        current = offline.current()
        assert current is not None
        return current[0]
    finally:
        await offline.stop()  # open step-ups stay pending: their expiry timers go with the runner


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
    parser.add_argument("--database-url", help="record the replay in this store (default: a throwaway SQLite)")
    args = parser.parse_args(argv)
    if args.policy and args.all:
        parser.error("--policy needs --scenario")
    if args.database_url and not args.policy:
        parser.error("--database-url needs --policy")

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
            db = open_store(args.database_url) if args.database_url else None
            try:
                replay = decide_all(pack, scenario_id, policy, args.signals, db=db)
            finally:
                if db is not None:
                    db.dispose()
            signals = "on" if args.signals else "off"
            print(f"\n### Decisions ({args.policy.name}, signals {signals}, run {replay.run_id})\n")
            print(_decisions_table(replay.decided))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
