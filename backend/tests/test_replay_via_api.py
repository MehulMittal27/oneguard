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
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from oneguard.llm.provider import NullProvider
from oneguard.replay.events import Pack
from oneguard.replay.oracle import ORACLE, unanswered_outcomes
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine
from tests.test_api_contract import Running, running, until

OUTCOME = {"approved": "approve", "stopped": "decline", "uncertain": "step_up"}
CAUTION = {"approve": 0, "step_up": 1, "decline": 2}


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


# Where the compiled reading of an instruction deliberately decides differently from the
# oracle's hand-built policy (docs/decisions.md, 2026-09-24, "A requested product gets its
# catalogue item type"): SCEN0002 compiles "Only sporting goods", so the shoes plus a monthly
# protection plan decline on the category (Q10's named alternative) instead of asking.
COMPILED = {"AU0018": ("decline", "item_mismatch")}


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_c6_outcomes_equal_the_oracle(api_run, scenario_id):
    actual = {source: OUTCOME[row["decision"]] for source, row in api_run[scenario_id].items()}
    expected = unanswered_outcomes(scenario_id)
    for source, (outcome, reason) in COMPILED.items():
        if source in expected:
            assert reason in api_run[scenario_id][source]["reason_codes"], source
            expected[source] = outcome
    assert actual == expected


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


# --- D2 decides by the card's own policy -----------------------------------------------

STRICTER = (
    "Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 300 or less. "
    "Do not add anything I did not ask for. Ask me when uncertain."
)


async def replay_scen0004(run: Running) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """D2 SCEN0004 on CA0039 at full speed; (D2's reply, {source id: C6 decision})."""
    r = await run.post("/api/dev/replay/restart", json={"scenario_id": "SCEN0004", "card_id": "CA0039", "speed_ms": 0})
    assert r.status_code == 200, r.text
    started = r.json()
    attempts = Pack.load().attempts_for("SCEN0004")

    async def finished() -> bool:
        status = (await run.get("/api/dev/replay")).json()
        return not status["running"] and status["delivered"] == len(attempts)

    await until(finished, timeout=60)
    rows = sorted(
        (d for d in await run.decisions("CU0019") if d["run_id"] == started["ledger_run_id"]),
        key=lambda d: d["occurred_at"],
    )
    return started, {a["authorization_id"]: row for a, row in zip(attempts, rows, strict=True)}


async def stored_current_run(db_url: str) -> dict[str, Any]:
    """D7 from a fresh process: the replay read back from its ``runs`` row."""
    async with running(db_url, provider=NullProvider()) as run:
        return (await run.get("/api/dev/runs/current")).json()


async def confirm_stricter(run: Running) -> dict[str, Any]:
    """The customer types ``STRICTER`` on CA0039 and confirms it on their device (C1, C2)."""
    r = await run.post("/api/cards/CA0039/policy-drafts", json={"instruction": STRICTER})
    assert r.status_code == 200, r.text
    draft = r.json()
    r = await run.post(
        f"/api/policy-drafts/{draft['draft_id']}/confirm",
        json={"checks": draft["checks"], "uncertainty_policy": draft["uncertainty_policy"], "open_questions": []},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_replay_decides_by_the_cards_active_policy(seeded: Path, tmp_path: Path) -> None:
    """The customer types a stricter policy on CA0039 (CHF 300 per order), confirms it on
    their device, then SCEN0004 is replayed: AU0035 (CHF 289) approves, AU0038 (CHF 391.50)
    declines over CHF 300, and the run says it decided by the card's policy."""

    db = fresh_db(seeded, tmp_path, "card")

    async def scenario() -> None:
        async with running(db, implementations=None, stubbed=None, provider=NullProvider()) as run:
            mandate = await confirm_stricter(run)
            started, rows = await replay_scen0004(run)
            assert (started["policy_source"], started["mandate_id"]) == ("card", mandate["mandate_id"])
            assert rows["AU0035"]["decision"] == "approved"
            assert rows["AU0035"]["message"] == "Approved CHF 289.00: it is within the limits you set."
            assert rows["AU0038"]["decision"] == "stopped"
            assert "per_order_limit_exceeded" in rows["AU0038"]["reason_codes"]
            assert rows["AU0038"]["message"] == "Declined CHF 391.50: CHF 391.50 is over your CHF 300.00 limit."
            current = (await run.get("/api/dev/runs/current")).json()
            assert (current["policy_source"], current["mandate_id"]) == ("card", mandate["mandate_id"])
            run.clock.offset += timedelta(minutes=1)
            assert (await run.post("/api/cards/CA0039/policy/revoke")).status_code == 204  # after the run
        stored = await stored_current_run(db)
        assert (stored["policy_source"], stored["mandate_id"]) == ("card", mandate["mandate_id"])

    asyncio.run(scenario())


def test_replay_under_a_revoked_policy_declines_every_purchase(seeded: Path, tmp_path: Path) -> None:
    """The card's last policy is revoked: the replay runs under it, not the scenario's, so
    every purchase declines at step 1, and the run says the policy is revoked."""
    db = fresh_db(seeded, tmp_path, "revoked")

    async def scenario() -> None:
        async with running(db, implementations=None, stubbed=None, provider=NullProvider()) as run:
            mandate = await confirm_stricter(run)
            r = await run.post("/api/cards/CA0039/policy/revoke")
            assert r.status_code == 204, r.text
            started, rows = await replay_scen0004(run)
            assert (started["policy_source"], started["mandate_id"]) == ("revoked", mandate["mandate_id"])
            assert (await run.get("/api/dev/runs/current")).json()["policy_source"] == "revoked"
            for source, row in rows.items():
                assert row["decision"] == "stopped", source
                assert {"card_or_authority_inactive", "no_active_policy"} & set(row["reason_codes"]), source
        assert (await stored_current_run(db))["policy_source"] == "revoked"

    asyncio.run(scenario())


def test_replay_without_a_policy_compiles_the_scenario_and_says_so(seeded: Path, tmp_path: Path) -> None:
    """A card that never had a policy: the scenario's own instruction (CHF 400) decides, for
    this replay only, and D2, D1 and D7 say the policy was compiled from the scenario."""

    db = fresh_db(seeded, tmp_path, "scenario")

    async def scenario() -> None:
        async with running(db, implementations=None, stubbed=None, provider=NullProvider()) as run:
            assert (await run.get("/api/cards/CA0039/policy")).json()["mandate"] is None
            started, rows = await replay_scen0004(run)
            assert (started["policy_source"], started["mandate_id"]) == ("scenario", "replay-SCEN0004")
            for path in ("/api/dev/replay", "/api/dev/runs/current"):
                assert (await run.get(path)).json()["policy_source"] == "scenario", path
            assert rows["AU0035"]["decision"] == "approved"
            assert rows["AU0038"]["decision"] == "uncertain"  # within CHF 400: the monitor is already bought
            assert "already_fulfilled" in rows["AU0038"]["reason_codes"]
            assert (await run.get("/api/cards/CA0039/policy")).json()["mandate"] is None  # nothing stored
        assert (await stored_current_run(db))["policy_source"] == "scenario"

    asyncio.run(scenario())
