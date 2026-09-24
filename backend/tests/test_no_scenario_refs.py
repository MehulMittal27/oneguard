"""CLAUDE.md rule 3: nothing keys on scenario ids, AU… ids or replay_order.

Scenario ids may appear only in replay/ and api/routes_dev.py. Everything that decides,
compiles or serves the customer is scanned, including comments and docstrings: an id in
a comment is how an id in code starts.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "oneguard"
SCANNED = ("engine", "compiler", "llm", "api")
ALLOWED = {PACKAGE / "api" / "routes_dev.py"}
FORBIDDEN = re.compile(r"SCEN\d|AU0\d|replay_order")


def offending_lines(root: Path, scanned=SCANNED, allowed=frozenset()) -> list[str]:
    found = []
    for folder in scanned:
        for path in sorted((root / folder).rglob("*.py")):
            if path in allowed:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if FORBIDDEN.search(line):
                    # as_posix: the same report on Windows, where str() would use backslashes
                    found.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
    return found


def test_no_scenario_references_in_decision_code():
    assert offending_lines(PACKAGE, allowed=ALLOWED) == []


def test_the_scanner_catches_each_pattern(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "bad.py").write_text(
        'if sid == "SCEN0004": pass\nx = "AU0037"\norder = e["replay_order"]\nok = "AUTH"\n',
        encoding="utf-8",
    )
    assert len(offending_lines(tmp_path)) == 3


def test_the_allowlist_is_only_routes_dev(tmp_path):
    api = tmp_path / "api"
    api.mkdir()
    (api / "routes_dev.py").write_text('SCENARIO = "SCEN0001"\n', encoding="utf-8")
    (api / "routes_customer.py").write_text('SCENARIO = "SCEN0001"\n', encoding="utf-8")
    hits = offending_lines(tmp_path, allowed={api / "routes_dev.py"})
    assert hits == ['api/routes_customer.py:1: SCENARIO = "SCEN0001"']
