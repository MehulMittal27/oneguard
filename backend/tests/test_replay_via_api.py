"""All 45 public purchases through the running API equal the acceptance oracle (P5-4).

The real app (lifespan, store, offline replay), every engine lane real and none stubbed,
no language model (``NullProvider``: D2 compiles each scenario's instruction with the
rule-based fallback parser). Each scenario is replayed through D2 and read back through
C6, the customer's own feed, so this checks what the customer sees, not the engine's
return value. Step-ups are never answered here (CLAUDE.md rule 6), so the oracle rows
that depend on an earlier answer are read in their "left pending" / "no yes" branch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from oneguard.llm.provider import NullProvider
from oneguard.replay.events import Pack
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine
from tests.test_api_contract import Running, running, until
from tests.test_oracle import ORACLE, Branch, branches, expected_outcome

OUTCOME = {"approved": "approve", "stopped": "decline", "uncertain": "step_up"}
CAUTION = {"approve": 0, "step_up": 1, "decline": 2}


def unanswered_branch(scenario_id: str) -> Branch | None:
    """The oracle branch in which no earlier step-up was answered yes: 'pending' when the
    oracle has one, else 'declined or expired' (a missing yes keeps the watch on)."""
    options = [b for b in branches(scenario_id) if b is not None]
    for answer in ("pending", "decline"):
        for branch in options:
            if branch.answer == answer:
                return branch
    return None


def expected(scenario_id: str) -> dict[str, str]:
    branch = unanswered_branch(scenario_id)
    return {
        row["id"]: expected_outcome(row, branch, ORACLE["defaults"])
        for row in ORACLE["scenarios"][scenario_id]["purchases"]
    }


def scenario_cards(pack: Pack) -> dict[str, tuple[str, str]]:
    """scenario id → (card id, customer id), from the scenario's authority."""
    cards = {}
    for scenario_id in pack.scenario_ids():
        authority = pack.authorities[pack.attempts_for(scenario_id)[0]["authority_id"]]
        cards[scenario_id] = (authority["card_id"], authority["customer_id"])
    return cards


async def replay_through_api(run: Running, pack: Pack, scenario_id: str) -> dict[str, dict[str, Any]]:
    """D2 one scenario at full speed, wait for it, read the new rows back through C6.

    Returns {source authorization id: C6 decision}. Rows are matched to purchases by
    simulated time, which is strictly increasing within a scenario.
    """
    card_id, customer_id = scenario_cards(pack)[scenario_id]
    attempts = pack.attempts_for(scenario_id)
    before = {d["authorization_id"] for d in await run.decisions(customer_id)}
    r = await run.post("/api/dev/replay/restart", json={"scenario_id": scenario_id, "card_id": card_id, "speed_ms": 0})
    assert r.status_code == 200, r.text

    async def finished() -> bool:
        status = (await run.get("/api/dev/replay")).json()
        return not status["running"] and status["delivered"] == len(attempts)

    await until(finished, timeout=60)
    rows = [d for d in await run.decisions(customer_id) if d["authorization_id"] not in before]
    assert len(rows) == len(attempts), scenario_id
    rows.sort(key=lambda d: d["occurred_at"])
    return {a["authorization_id"]: row for a, row in zip(attempts, rows, strict=True)}


async def replay_all(db_url: str, *, models: bool) -> dict[str, dict[str, dict[str, Any]]]:
    """Every scenario through one running app. ``models`` is the D5 switch."""
    pack = Pack.load()
    signals = "keywords" if models else "off"
    async with running(db_url, implementations=None, stubbed=None, provider=NullProvider(),
                       signals_backend=signals) as run:  # fmt: skip
        r = await run.post("/api/dev/soft-signals", json={"enabled": models})
        assert r.status_code == 200, r.text
        return {sid: await replay_through_api(run, pack, sid) for sid in pack.scenario_ids()}


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("replay-api") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


def fresh_db(seeded: Path, tmp_path: Path, name: str) -> str:
    copy = tmp_path / f"{name}.sqlite"
    copy.write_bytes(seeded.read_bytes())
    return f"sqlite:///{copy}"


@pytest.fixture(scope="module")
def api_run(seeded: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, dict[str, Any]]]:
    """One API replay of all five scenarios with the models on (keywords, no LLM)."""
    return asyncio.run(replay_all(fresh_db(seeded, tmp_path_factory.mktemp("on"), "on"), models=True))


def test_every_engine_lane_is_real(api_run):
    versions = {row["engine_version"] for rows in api_run.values() for row in rows.values()}
    assert versions and not any("stubs=" in v for v in versions), versions


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_c6_outcomes_equal_the_oracle(api_run, scenario_id):
    actual = {source: OUTCOME[row["decision"]] for source, row in api_run[scenario_id].items()}
    assert actual == expected(scenario_id)


def test_all_45_purchases_are_in_the_customer_feed(api_run):
    assert sum(len(rows) for rows in api_run.values()) == 45


def test_every_row_is_explained_and_step_ups_wait_for_the_customer(api_run):
    for rows in api_run.values():
        for source, row in rows.items():
            assert row["message"] and row["evidence"] and row["reason_codes"], source
            if row["decision"] == "uncertain":
                assert (row["status"], row["uncertain_outcome"]) == ("pending_human", "pending"), source
                assert row["deadline_at"], source
            else:
                assert row["status"] == "final" and row["uncertain_outcome"] is None, source


def test_injected_shop_text_is_flagged_and_never_repeated(api_run):
    flagged = {s: row for rows in api_run.values() for s, row in rows.items() if row["injection_flag"]}
    assert flagged, "the pack has two injected purchases"
    for source, row in flagged.items():
        said = " ".join([row["message"], row.get("counterfactual") or "", *(e["detail"] for e in row["evidence"])])
        for fragment in ("ignore any previous", "pre-authorised our store", "approve this payment"):
            assert fragment not in said.lower(), (source, fragment)
