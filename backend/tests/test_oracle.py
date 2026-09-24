"""Acceptance oracle: the 45 public purchases decide as docs/acceptance-oracle.yaml says.

Two layers:
- The oracle itself is checked against the data pack now (ids, order, instructions,
  totals, policy fixtures), so a typo in the oracle can never pass as an engine result.
- The end-to-end run replays every scenario through `pipeline.decide_event` with a fresh
  ledger, once per `depends` branch, with soft signals on and off, and compares each
  outcome. It runs on P2's StoreLedger and on the InMemoryLedger reference; the
  reference has no session watch, so scenarios whose expected outcomes cite it are
  strict xfail on the reference only.

Test data may name scenario and AU ids; the engine may not (test_no_scenario_refs.py).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy.orm import Session

from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger
from oneguard.engine.types import Policy
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex

REPO = Path(__file__).resolve().parents[2]
ORACLE = yaml.safe_load((REPO / "docs" / "acceptance-oracle.yaml").read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)  # real clock, deadlines only
POLICIES = Path(__file__).parent / "fixtures" / "policies"
OUTCOMES = {"approve", "decline", "step_up"}
FIELDS = {  # docs/api-contract.md §3.3
    "authorization.billing_amount_chf", "merchant.merchant_category",
    "merchant.familiar_on_card", "items[].item_category", "items[].size_eu",
    "order.return_window_days", "order.order_returnable", "cart.recurring",
    "items[].unit_price_chf", "items[].quantity", "merchant.merchant_country",
    "authorization.delivery_by", "authorization.weekday", "authorization.local_hour",
}
OPERATORS = {"<", "<=", "=", "!=", ">", ">=", "in", "not_in"}
LIMIT_WORDING = (  # docs/api-contract.md §3.9
    re.compile(r"^Total at or below CHF [\d,']+(\.\d+)? per order$", re.IGNORECASE),
    re.compile(r"^Total at or below CHF [\d,']+(\.\d+)? across any \d+ days$", re.IGNORECASE),
)


# --- reading the oracle -------------------------------------------------------------


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


def load_policy(scenario_id: str) -> dict:
    return yaml.safe_load((POLICIES / f"{scenario_id}.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pack() -> Pack:
    return Pack.load()


# --- the oracle agrees with the data pack -------------------------------------------


def test_oracle_covers_every_scenario(pack):
    assert set(ORACLE["scenarios"]) == set(pack.scenarios)


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_oracle_rows_are_the_pack_attempts_in_replay_order(pack, scenario_id):
    rows = ORACLE["scenarios"][scenario_id]["purchases"]
    assert [r["id"] for r in rows] == [a["authorization_id"] for a in pack.attempts_for(scenario_id)]


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_oracle_instruction_is_the_catalogue_instruction_verbatim(pack, scenario_id):
    catalogue = pack.scenarios[scenario_id]["cardholder_instruction"]
    assert ORACLE["scenarios"][scenario_id]["instruction"] == catalogue


def test_oracle_outcomes_and_amounts_are_well_formed(pack):
    billed = {a["authorization_id"]: float(a["billing_amount_chf"]) for a in pack.attempts}
    for scenario in ORACLE["scenarios"].values():
        for row in scenario["purchases"]:
            options = row.get("depends", [row])
            assert all(o["outcome"] in OUTCOMES for o in options), row["id"]
            if "chf" in row:  # amounts in the oracle are the pack's CHF totals
                assert row["chf"] == pytest.approx(billed[row["id"]]), row["id"]


def _totals(defaults: dict) -> Counter:
    counts: Counter = Counter()
    for scenario in ORACLE["scenarios"].values():
        for row in scenario["purchases"]:
            counts["depends" if "depends" in row else expected_outcome(row, None, defaults)] += 1
    return counts


def test_totals_under_defaults():
    assert _totals(ORACLE["defaults"]) == Counter(ORACLE["totals_under_defaults"])


def test_totals_with_q10_decline():
    defaults = {**ORACLE["defaults"], "q10_a6_without_c10": "decline"}
    assert _totals(defaults) == Counter(ORACLE["totals_with_q10_decline"])


def test_alt_under_card_level_known_shop_flips_only_the_other_card_purchase():
    defaults = {**ORACLE["defaults"], "q7_known_shop": "card"}
    rows = {r["id"]: r for s in ORACLE["scenarios"].values() for r in s["purchases"]}
    flipped = [
        i for i, r in rows.items()
        if "depends" not in r
        and expected_outcome(r, None, defaults) != expected_outcome(r, None, ORACLE["defaults"])
    ]
    assert len(flipped) == 1 and expected_outcome(rows[flipped[0]], None, defaults) == "decline"


def test_depends_branches_are_readable():
    for scenario_id in ORACLE["scenarios"]:
        for branch in branches(scenario_id):
            if branch is not None:
                assert branch.answer in {"approve", "pending", "decline"}


# --- the hand-built policies are valid test input ----------------------------------


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_policy_fixture_matches_its_scenario(pack, scenario_id):
    policy = load_policy(scenario_id)
    assert policy["scenario_id"] == scenario_id
    assert policy["instruction"] == pack.scenarios[scenario_id]["cardholder_instruction"]
    assert policy["uncertainty_policy"] in {"ask", "decline", "approve"}
    for rule in policy["rules"]:
        assert rule["field"] is None or rule["field"] in FIELDS, rule
        assert rule["operator"] is None or rule["operator"] in OPERATORS, rule
        assert rule["source"] in {"exact", "inferred"}, rule
        if rule["field"] == "authorization.billing_amount_chf":
            assert any(p.match(rule["text"]) for p in LIMIT_WORDING), rule["text"]
    ids = [rule["id"] for rule in policy["rules"]]
    assert "C1" in ids, "every public instruction states a per-order amount cap"


# --- end to end: every purchase through the pipeline -------------------------------


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> StoreHistoryIndex:
    """HistoryIndex over a freshly seeded temp SQLite store (no CSV reads in the engine)."""
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('oracle') / 'oneguard.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        index = StoreHistoryIndex.load(s)
    engine.dispose()
    return index


def to_policy(scenario_id: str) -> Policy:
    fixture = load_policy(scenario_id)
    fixture.pop("scenario_id")
    return Policy.model_validate({"mandate_id": f"TM_ORACLE_{scenario_id}", **fixture})


LEDGERS = ["store", "memory"]


@contextmanager
def fresh_ledger(kind: str, history: StoreHistoryIndex, folder: Path) -> Iterator[Ledger]:
    """An empty ledger: P2's StoreLedger on its own temp SQLite file (the one the worker
    uses, with the session watch), or the in-memory reference."""
    if kind == "memory":
        yield InMemoryLedger(history=history)
        return
    engine = make_engine(f"sqlite:///{folder / f'ledger-{uuid4().hex}.sqlite'}")
    init_db(engine)
    try:
        with Session(engine, expire_on_commit=False) as s:
            yield StoreLedger(s, history=history)
    finally:
        engine.dispose()


def _run_scenario(
    pack: Pack, history: StoreHistoryIndex, scenario_id: str, branch: Branch | None, signals: bool,
    ledger: Ledger,
) -> dict[str, str]:
    """Replay a scenario through the pipeline on an empty ledger: {source id: outcome}.

    The step-up a `branch` names is answered (approve / decline) before the next event,
    or left pending (reserved, M5). Every other step-up stays pending: no answer is
    invented (CLAUDE.md rule 6).
    """
    policy = to_policy(scenario_id)
    ctx = PipelineContext(
        policy=policy, ledger=ledger, history=history,
        run_id=f"oracle-{scenario_id}", signals_enabled=signals,
    )  # fmt: skip
    outcomes = {}
    for event in build_events(pack, scenario_id, mandate_id=policy.mandate_id, now=NOW):
        engine, _, _ = decide_event(event, ctx)
        auth = event["authorization"]
        outcomes[auth["source_authorization_id"]] = engine.outcome
        answers = branch is not None and branch.authorization_id == auth["source_authorization_id"]
        if answers and engine.outcome == "step_up" and branch.answer != "pending":
            ledger.resolve(auth["authorization_id"], branch.answer, "customer", NOW)
    return outcomes


@pytest.mark.parametrize("scenario_id", sorted(ORACLE["scenarios"]))
def test_policy_fixture_loads_as_the_engine_policy(scenario_id):
    policy = to_policy(scenario_id)
    assert any(rule.id == "C1" for rule in policy.rules)


def needs_session_watch(scenario_id: str) -> bool:
    """An expected outcome in the scenario cites the session watch (rules.md W-rule 4)."""
    rows = ORACLE["scenarios"][scenario_id]["purchases"]
    return any("session_watch" in option.get("rules", []) for row in rows for option in row.get("depends", [row]))


@pytest.mark.parametrize("kind", LEDGERS)
@pytest.mark.parametrize("signals", [False, True], ids=["signals-off", "signals-on"])
@pytest.mark.parametrize(
    ("scenario_id", "branch"),
    [(s, b) for s in sorted(ORACLE["scenarios"]) for b in branches(s)],
    ids=lambda v: v if isinstance(v, str) else (v.label if v else "no-depends"),
)
def test_oracle_outcomes(pack, history, monkeypatch, tmp_path, request, scenario_id, branch, signals, kind):
    if kind == "memory" and needs_session_watch(scenario_id):
        request.applymarker(pytest.mark.xfail(strict=True, reason="InMemoryLedger has no session watch"))
    monkeypatch.setenv("ONEGUARD_SOFT_SIGNALS", "keywords" if signals else "off")
    with fresh_ledger(kind, history, tmp_path) as ledger:
        actual = _run_scenario(pack, history, scenario_id, branch, signals, ledger)
    expected = {
        row["id"]: expected_outcome(row, branch, ORACLE["defaults"])
        for row in ORACLE["scenarios"][scenario_id]["purchases"]
    }
    assert actual == expected


@pytest.mark.parametrize("kind", LEDGERS)
def test_outcomes_identical_with_signals_on_and_off(pack, history, monkeypatch, tmp_path, kind):
    """P8: models only add evidence; the public outcomes do not move. Holds on stubs too."""
    for scenario_id in sorted(ORACLE["scenarios"]):
        for branch in branches(scenario_id):
            outcomes = []
            for signals in (False, True):
                monkeypatch.setenv("ONEGUARD_SOFT_SIGNALS", "keywords" if signals else "off")
                with fresh_ledger(kind, history, tmp_path) as ledger:
                    outcomes.append(_run_scenario(pack, history, scenario_id, branch, signals, ledger))
            assert outcomes[0] == outcomes[1], scenario_id


def test_the_store_ledger_run_is_durable(pack, history, tmp_path):
    """The store parametrisation really writes the decisions table (not a silent no-op)."""
    with fresh_ledger("store", history, tmp_path) as ledger:
        outcomes = _run_scenario(pack, history, "SCEN0001", None, False, ledger)
        stored = [ledger.get(source_id) for source_id in outcomes]
    assert all(entry is not None for entry in stored) and len(stored) == 10


MESSAGE = re.compile(r"^(Approved|Declined|Waiting for you) CHF \d+\.\d{2}: (?P<clause>[^—]+)\.$")


def test_every_message_is_one_clause_and_never_the_counterfactual(pack, history, tmp_path):
    """rules.md §9: "{Outcome} CHF {amount}: {clause}." for all 45 purchases; the clause is
    at most 15 words (an amount is one word) and "Would approve …" is only ever the
    counterfactual, which every decline has."""
    for scenario_id in sorted(ORACLE["scenarios"]):
        policy = to_policy(scenario_id)
        with fresh_ledger("store", history, tmp_path) as ledger:
            ctx = PipelineContext(policy=policy, ledger=ledger, history=history, run_id=f"messages-{scenario_id}")
            for event in build_events(pack, scenario_id, mandate_id=policy.mandate_id, now=NOW):
                engine, explanation, _ = decide_event(event, ctx)
                source_id = event["authorization"]["source_authorization_id"]
                m = MESSAGE.match(explanation.message)
                assert m, (source_id, explanation.message)
                assert len(re.sub(r"CHF [\d.]+", "CHF", m["clause"]).split()) <= 15, (source_id, explanation.message)
                assert "Would approve" not in explanation.message, source_id
                if engine.outcome == "decline":
                    assert (explanation.counterfactual or "").startswith("Would approve"), source_id
