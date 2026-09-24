"""Reading docs/acceptance-oracle.yaml: the expected outcome of every public purchase.

Test tooling like the rest of ``replay/``: it names scenario and AU ids, and the engine
never imports it (test_no_scenario_refs.py). The oracle test, the replay-through-API
test and the replay matrix all read expected outcomes and ``depends`` branches here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ORACLE_PATH = Path(__file__).resolve().parents[3] / "docs" / "acceptance-oracle.yaml"
ORACLE: dict[str, Any] = yaml.safe_load(ORACLE_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Branch:
    """One way the customer answers an earlier step-up that a later row depends on."""

    label: str
    authorization_id: str  # source id of the step-up being answered
    answer: str  # approve | pending | decline


def _branch_answer(condition: str) -> tuple[str, str]:
    source_id, _, rest = condition.partition(" ")
    if "approval" in rest:
        return source_id, "approve"
    if "pending" in rest:
        return source_id, "pending"
    if "declined" in rest or "expired" in rest:
        return source_id, "decline"
    raise ValueError(f"unreadable depends condition: {condition!r}")


def branches(scenario_id: str) -> list[Branch | None]:
    """Every `depends` branch in a scenario, or [None] when nothing depends."""
    found = []
    for row in ORACLE["scenarios"][scenario_id]["purchases"]:
        for option in row.get("depends", []):
            source_id, answer = _branch_answer(option["if"])
            found.append(Branch(option["if"], source_id, answer))
    return found or [None]


def unanswered_branch(scenario_id: str) -> Branch | None:
    """The oracle branch in which no earlier step-up was answered yes: 'pending' when the
    oracle has one, else 'declined or expired' (a missing yes keeps the watch on)."""
    options = [b for b in branches(scenario_id) if b is not None]
    for answer in ("pending", "decline"):
        for branch in options:
            if branch.answer == answer:
                return branch
    return None


def expected_outcome(row: dict, branch: Branch | None, defaults: dict) -> str:
    """The outcome for one oracle row under the team's defaults and a depends branch."""
    if "depends" in row:
        assert branch is not None, f"{row['id']} depends on an earlier answer"
        for option in row["depends"]:
            if _branch_answer(option["if"]) == (branch.authorization_id, branch.answer):
                return option["outcome"]
        raise AssertionError(f"{row['id']}: no depends option for {branch.label}")
    outcome = row["outcome"]
    for question, alternative in row.get("alt", {}).items():
        chosen = defaults[question]
        if isinstance(alternative, dict):  # {q7_known_shop: {card: decline}}
            outcome = alternative.get(chosen, outcome)
        elif chosen == alternative:  # {q10_a6_without_c10: decline}
            outcome = alternative
    return outcome


def unanswered_outcomes(scenario_id: str) -> dict[str, str]:
    """Source id → the oracle's outcome under its defaults when no step-up is answered."""
    branch = unanswered_branch(scenario_id)
    return {
        row["id"]: expected_outcome(row, branch, ORACLE["defaults"])
        for row in ORACLE["scenarios"][scenario_id]["purchases"]
    }
