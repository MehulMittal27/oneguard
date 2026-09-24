"""Data-pack check for facts.py + policy.py (lane P2).

Runs all 45 public purchases (P5's replay events) and their 56 cart lines through
build_facts and evaluate_rules with the team's policy fixtures, and compares with
docs/acceptance-oracle.yaml at the rule level:
  - approve  -> every customer rule passes
  - decline  -> at least one of the C-rules the oracle names fails
  - step_up  -> no customer rule fails (the ask comes from an unknown or a protection)
The period limit (C2) is checked with evaluate_period_rule and the oracle's own approvals.

Test data only: scenario and purchase ids are allowed here (not in engine/).
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from oneguard.engine.facts import _HAS_SIZE_LETTER, build_facts
from oneguard.engine.policy import evaluate_period_rule, evaluate_rules, period_rules
from oneguard.engine.types import STEP1_RULE_IDS, Policy
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.history import StoreHistoryIndex

REPO = Path(__file__).resolve().parents[2]
ORACLE = yaml.safe_load((REPO / "docs" / "acceptance-oracle.yaml").read_text(encoding="utf-8"))
POLICIES = Path(__file__).parent / "fixtures" / "policies"
PACK = Pack.load()
EVENTS = {sid: build_events(PACK, sid) for sid in PACK.scenario_ids()}


def _policy(scenario_id: str) -> Policy:
    fixture = yaml.safe_load((POLICIES / f"{scenario_id}.yaml").read_text(encoding="utf-8"))
    fixture.pop("scenario_id")
    return Policy.model_validate({"mandate_id": f"TM_P2_{scenario_id}", **fixture})


def _oracle() -> dict[str, tuple[str, set[str]]]:
    """purchase id -> (outcome, C-rule ids). For 'depends' rows, the branch where the
    earlier ask was declined (the replay's scripted customer)."""
    out = {}
    for s in ORACLE["scenarios"].values():
        for p in s["purchases"]:
            if "depends" in p:
                branch = next(d for d in p["depends"] if "declined" in d["if"])
                outcome, rules = branch["outcome"], branch["rules"]
            else:
                outcome, rules = p["outcome"], p.get("rules", [])
            out[p["id"]] = (outcome, {r for r in rules if r.startswith("C") and r != "C11"})
    return out


ORACLE_ROWS = _oracle()


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list]:
    """Rule results for all 45 purchases, familiarity from a freshly seeded store."""
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('p2') / 'oneguard.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        history = StoreHistoryIndex.load(s)
    engine.dispose()

    customer = {sid: PACK.authorities[PACK.attempts_for(sid)[0]["authority_id"]]["customer_id"]
                for sid in PACK.scenario_ids()}
    out = {}
    for sid, events in EVENTS.items():
        policy, known = _policy(sid), set(history.known_merchants(customer[sid]))
        finals: list = []
        for event in events:
            f = build_facts(event, history)
            f = f.model_copy(update={"merchant_known": f.merchant_id in known})
            rs = evaluate_rules(f, policy)
            for rule in period_rules(policy):
                start = f.timestamp - timedelta(days=rule.period_days or 7)
                spent = sum(x for ts, x in finals if start < ts < f.timestamp)
                rs.append(evaluate_period_rule(rule, f, spent, 0))
            pid = f.source_authorization_id
            if ORACLE_ROWS[pid][0] == "approve":
                finals.append((f.timestamp, f.billing_amount_chf))
            out[pid] = rs
    return out


def test_every_purchase_is_covered(results):
    assert len(results) == 45 and set(results) == set(ORACLE_ROWS)


@pytest.mark.parametrize("purchase_id", sorted(ORACLE_ROWS))
def test_customer_rules_match_oracle(results, purchase_id):
    outcome, expected = ORACLE_ROWS[purchase_id]
    rs = [r for r in results[purchase_id] if r.rule_id not in STEP1_RULE_IDS]
    fails = {r.rule_id for r in rs if r.outcome == "fail"}
    not_passing = {r.rule_id: f"{r.outcome}: {r.detail}" for r in rs if r.outcome != "pass"}
    if outcome == "approve":
        assert not not_passing, f"approved in the oracle, but: {not_passing}"
    elif outcome == "decline":
        if expected:
            assert expected & fails, f"oracle names {sorted(expected)}; our fails: {sorted(fails)}"
    else:  # step_up: nothing may fail; the ask comes from an unknown or a protection
        assert not fails, f"step_up in the oracle, but rules fail: {not_passing}"


def test_step1_passes_on_every_public_purchase(results):
    for pid, rs in results.items():
        assert all(r.outcome == "pass" for r in rs if r.rule_id in STEP1_RULE_IDS), pid


def test_every_fail_explains_itself(results):
    for pid, rs in results.items():
        for r in rs:
            if r.outcome == "fail":
                assert r.detail and r.counterfactual, f"{pid} {r.rule_id} has no explanation"


# (purchase, line, size_eu, size_letter, return_days | 'not_stated' | None, recurring True | None)
# Reviewed line by line against the shop text; see the PR description table.
EXPECTED_LINES = [
    ('AU0001', 1, None, None, None, None),
    ('AU0002', 1, None, None, None, None),
    ('AU0002', 2, None, None, None, None),
    ('AU0003', 1, None, None, None, None),
    ('AU0003', 2, None, None, None, None),
    ('AU0004', 1, None, None, None, None),
    ('AU0004', 2, None, None, None, None),
    ('AU0005', 1, None, None, None, None),
    ('AU0005', 2, None, None, None, None),
    ('AU0006', 1, None, None, None, None),
    ('AU0006', 2, None, None, None, None),
    ('AU0007', 1, None, None, None, None),
    ('AU0007', 2, None, None, None, None),
    ('AU0008', 1, None, None, None, None),
    ('AU0008', 2, None, None, None, None),
    ('AU0009', 1, None, None, None, None),
    ('AU0010', 1, None, None, None, None),
    ('AU0010', 2, None, None, None, None),
    ('AU0011', 1, None, None, None, None),
    ('AU0011', 2, None, None, None, None),
    ('AU0012', 1, 43.0, None, 30, None),
    ('AU0013', 1, 42.0, None, 30, None),
    ('AU0014', 1, 43.0, None, 0, None),
    ('AU0015', 1, 43.0, None, 7, None),
    ('AU0016', 1, 43.0, None, 'not_stated', None),
    ('AU0017', 1, 43.0, None, 30, None),
    ('AU0018', 1, 43.0, None, 30, None),
    ('AU0018', 2, None, None, None, True),
    ('AU0019', 1, 43.0, None, 14, None),
    ('AU0020', 1, None, 'M', 30, None),
    ('AU0021', 1, 43.0, None, 30, None),
    ('AU0022', 1, 43.0, None, 30, None),
    ('AU0023', 1, 43.0, None, 30, None),
    ('AU0024', 1, None, 'S', 30, None),
    ('AU0025', 1, None, 'S', 30, None),
    ('AU0026', 1, None, 'S', 30, None),
    ('AU0027', 1, None, 'M', 14, None),
    ('AU0028', 1, None, 'M', 14, None),
    ('AU0029', 1, None, 'M', 14, None),
    ('AU0030', 1, None, 'M', 14, None),
    ('AU0031', 1, None, 'S', 30, None),
    ('AU0032', 1, None, 'S', 30, None),
    ('AU0033', 1, None, 'S', 30, None),
    ('AU0034', 1, None, 'M', 30, None),
    ('AU0035', 1, None, None, 14, None),
    ('AU0036', 1, None, None, 14, None),
    ('AU0037', 1, None, None, 14, None),
    ('AU0038', 1, None, None, 14, None),
    ('AU0039', 1, None, None, 14, None),
    ('AU0040', 1, None, None, 14, None),
    ('AU0041', 1, None, None, 14, None),
    ('AU0041', 2, None, None, None, None),
    ('AU0042', 1, None, None, 14, None),
    ('AU0043', 1, None, None, None, None),
    ('AU0044', 1, None, None, 14, None),
    ('AU0045', 1, None, None, 14, None),
]


ALL_LINES = {(e["authorization"]["source_authorization_id"], ln["line_no"]): e
             for events in EVENTS.values() for e in events for ln in e["authorization"]["items"]}


def test_expected_lines_cover_all_56_cart_lines():
    assert len(EXPECTED_LINES) == 56 == len(ALL_LINES)


@pytest.mark.parametrize("pid,line,size_eu,size_letter,returns,recurring", EXPECTED_LINES)
def test_facts_on_every_cart_line(pid, line, size_eu, size_letter, returns, recurring):
    item = next(i for i in build_facts(ALL_LINES[(pid, line)], None).items if i.line_no == line)
    assert (item.size_eu.value if item.size_eu.known else None) == size_eu
    if _HAS_SIZE_LETTER:
        assert (item.size_letter.value if item.size_letter.known else None) == size_letter
    got_returns = (item.return_window_days.value if item.return_window_days.known
                   else "not_stated" if "not stated by seller" in item.return_window_days.detail else None)
    assert got_returns == returns
    assert (True if item.recurring.known else None) == recurring
