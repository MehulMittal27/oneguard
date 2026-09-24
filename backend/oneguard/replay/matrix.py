"""The 45-row replay matrix for the slide: ``python -m oneguard.replay.matrix`` (``make matrix``).

Every public purchase through the real pipeline (``runner.decide_all``: fresh
``StoreLedger`` per scenario, every engine lane registered), once with soft signals on
and once off, next to the acceptance oracle. Prints markdown: a summary, then one row
per purchase with the outcome, the oracle's outcome, whether they match, whether
signals off gave the same outcome, and the message the customer sees.

Test tooling like the rest of ``replay/``: it may read scenario ids, the hand-built
policy fixtures and the oracle. Step-ups are never answered, so an oracle row that
depends on an earlier answer is read in its "left pending" branch, else its "no yes"
branch (as tests/test_replay_via_api.py does).
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from oneguard.replay.events import Pack
from oneguard.replay.runner import _cell, decide_all, load_policy

BACKEND = Path(__file__).resolve().parents[2]
POLICIES = BACKEND / "tests" / "fixtures" / "policies"
ORACLE = BACKEND.parent / "docs" / "acceptance-oracle.yaml"
LEADS = {"approve": "approve", "decline": "decline", "step_up": "ask"}


def _unanswered(condition: str) -> int:
    """Preference for the branch nobody answered: pending first, then decline/expired."""
    if "pending" in condition:
        return 0
    if "declined" in condition or "expired" in condition:
        return 1
    return 2


def expected_outcomes(oracle: dict) -> dict[str, str]:
    """Source id → the oracle's outcome under its defaults, step-ups left unanswered."""
    expected = {}
    for scenario in oracle["scenarios"].values():
        for row in scenario["purchases"]:
            if "depends" in row:
                option = min(row["depends"], key=lambda o: _unanswered(o["if"]))
                expected[row["id"]] = option["outcome"]
            else:
                expected[row["id"]] = row["outcome"]
    return expected


def build(pack: Pack, policies: Path, oracle: dict) -> tuple[list[str], list[dict]]:
    expected = expected_outcomes(oracle)
    rows = []
    for scenario_id in pack.scenario_ids():
        policy = load_policy(policies / f"{scenario_id}.yaml", mandate_id=f"TM_REPLAY_{scenario_id}")
        on = decide_all(pack, scenario_id, policy, signals=True)
        off = {source: outcome for source, outcome, _, _ in decide_all(pack, scenario_id, policy, signals=False)}
        for source, outcome, message, counterfactual in on:
            rows.append({
                "id": source, "scenario": scenario_id, "outcome": outcome,
                "expected": expected.get(source, "?"), "off": off[source],
                "message": message, "counterfactual": counterfactual,
            })  # fmt: skip
    totals = Counter(r["outcome"] for r in rows)
    matched = sum(r["outcome"] == r["expected"] for r in rows)
    same = sum(r["outcome"] == r["off"] for r in rows)
    summary = [
        f"- Purchases: {len(rows)}",
        f"- Outcomes: {totals['approve']} approve, {totals['decline']} decline, {totals['step_up']} ask",
        f"- Oracle match: {matched}/{len(rows)}",
        f"- Signals off vs on: {same}/{len(rows)} identical",
    ]
    return summary, rows


def markdown(summary: list[str], rows: list[dict]) -> str:
    lines = ["# Replay matrix", "", *summary, "",
             "| ID | Scenario | Outcome | Oracle | Match | Signals off | Message |",
             "|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in rows:
        match = "✓" if r["outcome"] == r["expected"] else "✗"
        off = "same" if r["off"] == r["outcome"] else LEADS.get(r["off"], r["off"])
        message = r["message"]  # explain already ends a decline with its counterfactual
        lines.append(
            f"| {r['id']} | {r['scenario']} | {LEADS[r['outcome']]} | {LEADS.get(r['expected'], r['expected'])} "
            f"| {match} | {off} | {_cell(message)} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the 45-row replay matrix as markdown.")
    parser.add_argument("--policies", type=Path, default=POLICIES, help="folder of <scenario>.yaml policies")
    parser.add_argument("--oracle", type=Path, default=ORACLE, help="acceptance oracle YAML")
    args = parser.parse_args(argv)
    oracle = yaml.safe_load(args.oracle.read_text(encoding="utf-8"))
    summary, rows = build(Pack.load(), args.policies, oracle)
    print(markdown(summary, rows))
    return 0 if all(r["outcome"] == r["expected"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
