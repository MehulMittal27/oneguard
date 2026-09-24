"""replay/matrix.py: the slide's 45-row matrix (make matrix)."""

from __future__ import annotations

import yaml

from oneguard.replay import matrix
from tests.test_replay_via_api import expected


def test_expected_outcomes_read_the_unanswered_branch_like_the_api_test():
    oracle = yaml.safe_load(matrix.ORACLE.read_text(encoding="utf-8"))
    mine = matrix.expected_outcomes(oracle)
    assert len(mine) == 45
    for scenario_id in oracle["scenarios"]:
        assert {i: mine[i] for i in expected(scenario_id)} == expected(scenario_id), scenario_id


def test_markdown_has_a_summary_and_one_row_per_purchase():
    rows = [
        {"id": "AU1", "scenario": "S", "outcome": "approve", "expected": "approve", "off": "approve",
         "message": "Approved CHF 1.00: ok.", "counterfactual": ""},
        {"id": "AU2", "scenario": "S", "outcome": "step_up", "expected": "decline", "off": "decline",
         "message": "Waiting for you CHF 2.00: a | b.", "counterfactual": "x"},
    ]  # fmt: skip
    text = matrix.markdown(["- Oracle match: 1/2"], rows)
    lines = text.splitlines()
    assert lines[0] == "# Replay matrix" and "- Oracle match: 1/2" in lines
    assert "| AU1 | S | approve | approve | ✓ | same | Approved CHF 1.00: ok. |" in lines
    assert "| AU2 | S | ask | decline | ✗ | decline | Waiting for you CHF 2.00: a \\| b. |" in lines
