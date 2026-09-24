"""Chaos: every model off, the engine stays safe (P5-4; rules.md P5, P8; CLAUDE.md rule 1).

"Models off" is soft signals off and no language model (``NullProvider``), the state
the D5 toggle and ``ONEGUARD_SOFT_SIGNALS=off`` / ``ONEGUARD_LLM_PROVIDER=null`` give.
Switching them off may only make a decision more cautious (a step-up where a model
would have resolved a fact), never less. On the 45 public purchases the outcomes are
identical: the deterministic gate decides, the models only add evidence.
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from oneguard.engine.ledger_base import InMemoryLedger
from oneguard.llm.provider import NullProvider
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex
from tests.test_oracle import to_policy
from tests.test_replay_via_api import CAUTION, OUTCOME, fresh_db, replay_all

# --- through the API: models off vs on, all 45 purchases ------------------------------


@pytest.fixture(scope="module")
def seeded_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("chaos") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture(scope="module")
def on_and_off(seeded_path: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    folder = tmp_path_factory.mktemp("chaos-runs")
    on = asyncio.run(replay_all(fresh_db(seeded_path, folder, "on"), models=True))
    off = asyncio.run(replay_all(fresh_db(seeded_path, folder, "off"), models=False))
    return on, off


def _outcomes(run: dict[str, dict[str, dict[str, Any]]]) -> dict[str, str]:
    return {source: OUTCOME[row["decision"]] for rows in run.values() for source, row in rows.items()}


def test_models_off_is_never_less_cautious(on_and_off):
    on, off = (_outcomes(run) for run in on_and_off)
    assert set(on) == set(off) and len(on) == 45
    looser = {s: (on[s], off[s]) for s in on if CAUTION[off[s]] < CAUTION[on[s]]}
    assert looser == {}, "switching models off made these less cautious"


def test_models_off_gives_identical_public_outcomes(on_and_off):
    on, off = (_outcomes(run) for run in on_and_off)
    assert off == on


def test_models_off_is_recorded_on_every_decision(on_and_off):
    on, off = on_and_off
    assert all("signals=on" in row["engine_version"] for rows in on.values() for row in rows.values())
    assert all("signals=off" in row["engine_version"] for rows in off.values() for row in rows.values())


# --- where a model does matter: tier 2 reads a fact the English regex cannot -----------

GERMAN = "Straßenlaufschuh, Größe 43; Rückgabe innerhalb von 30 Tagen"


class FakeModel:
    """A provider that answers tier 2's schema with fixed facts and counts its calls."""

    def __init__(self, size_eu: float | None, return_window_days: int | None) -> None:
        self.answer = {"size_eu": size_eu, "return_window_days": return_window_days}
        self.calls = 0

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        self.calls += 1
        return {"lines": [{"line_no": 1, "size_letter": None, **self.answer}]}


@pytest.fixture(scope="module")
def history(seeded_path: Path) -> StoreHistoryIndex:
    engine = make_engine(f"sqlite:///{seeded_path}")
    with session(engine) as s:
        index = StoreHistoryIndex.load(s)
    engine.dispose()
    return index


def _shoe_purchase(details: str) -> dict:
    """The pack's first compliant running-shoe purchase, with its product text replaced."""
    [event] = [e for e in build_events(Pack.load(), "SCEN0002") if e["authorization"]["replay_order"] == 1]
    event = copy.deepcopy(event)
    event["authorization"]["items"][0]["item_details"] = details
    return event


def _decide(event: dict, history: StoreHistoryIndex, provider: Any, signals: bool) -> str:
    ctx = PipelineContext(
        policy=to_policy("SCEN0002"), ledger=InMemoryLedger(history=history), history=history,
        run_id="chaos", provider=provider, signals_enabled=signals,
    )  # fmt: skip
    engine, _, _ = decide_event(event, ctx)
    return engine.outcome


def test_the_regex_alone_cannot_read_the_german_text(history):
    assert _decide(_shoe_purchase(GERMAN), history, NullProvider(), signals=False) == "step_up"


def test_a_model_may_resolve_what_models_off_leaves_to_the_customer(history):
    model = FakeModel(size_eu=43, return_window_days=30)
    on = _decide(_shoe_purchase(GERMAN), history, model, signals=True)
    off = _decide(_shoe_purchase(GERMAN), history, NullProvider(), signals=False)
    assert model.calls == 1
    assert (on, off) == ("approve", "step_up"), "off is more cautious, never less"


def test_a_model_that_invents_a_fact_approves_nothing(history):
    """"Größe 43" with no return terms: a model claiming 30 days is not grounded in the text."""
    model = FakeModel(size_eu=43, return_window_days=30)
    outcome = _decide(_shoe_purchase("Straßenlaufschuh, Größe 43"), history, model, signals=True)
    assert outcome == "step_up"


def test_a_model_contradicting_the_text_cannot_approve(history):
    """The text says size 42; a model saying 43 is refused, and the size rule still fails."""
    model = FakeModel(size_eu=43, return_window_days=30)
    event = _shoe_purchase("Straßenlaufschuh, Größe 42; Rückgabe innerhalb von 30 Tagen")
    on = _decide(event, history, model, signals=True)
    off = _decide(_shoe_purchase("Straßenlaufschuh, Größe 42; Rückgabe innerhalb von 30 Tagen"),
                  history, NullProvider(), signals=False)  # fmt: skip
    assert on != "approve" and CAUTION[off] >= CAUTION[on]
