"""replay/matrix.py: docs/replay-matrix.md is the matrix the pipeline gives today (make matrix)."""

from __future__ import annotations

import pytest

from oneguard.engine import signals
from oneguard.replay import matrix
from oneguard.replay.events import Pack
from oneguard.replay.runner import Decided


@pytest.fixture(scope="module")
def pack() -> Pack:
    return Pack.load()


@pytest.fixture(scope="module")
def rows(pack: Pack) -> list[matrix.Row]:
    return matrix.build(pack)


def test_every_public_purchase_matches_the_oracle_with_signals_on_and_off(rows):
    assert len(rows) == 45
    assert matrix.problems(rows) == []


def test_the_committed_matrix_is_current(pack, rows):
    """A change to a decision, reason code or message regenerates the file: `make matrix`."""
    assert matrix.DOC.read_text(encoding="utf-8") == matrix.markdown(pack, rows), "run `make matrix`"


def test_the_matrix_uses_keyword_signals_whatever_the_environment(monkeypatch):
    monkeypatch.setattr(signals, "BACKEND", signals.select_backend("off"))
    with matrix._keyword_signals():
        assert isinstance(signals.BACKEND, signals.KeywordSignals)
    assert not isinstance(signals.BACKEND, signals.KeywordSignals)


def test_problems_names_an_oracle_miss_and_a_signal_move():
    decided = Decided("AU1", "step_up", ["x"], "Waiting for you: a | b.", "")
    found = matrix.problems([matrix.Row(decided, expected="decline", off="approve")])
    assert found == ["AU1: step_up, the oracle says decline", "AU1: step_up with signals on, approve with signals off"]


def test_a_pipe_in_a_message_stays_in_its_cell(pack):
    decided = Decided("AU1", "step_up", ["a", "b"], "Waiting for you: a | b.", "c | d")
    text = matrix.markdown(pack, [matrix.Row(decided, expected="step_up", off="step_up")])
    assert "| AU1 | step_up | a, b | Waiting for you: a \\| b. | c \\| d |" in text.splitlines()
