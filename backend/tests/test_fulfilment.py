"""A8 already bought: a single-item mandate is fulfilled by its first final approval.

"Buy the camera lens I chose" asks for one lens. The live run that bought it eight times
(docs/decisions.md 2026-09-25) is the bug: a later purchase of the requested item under
the same mandate steps up with ``already_fulfilled``. Declines and unanswered, declined
or expired step-ups fulfil nothing, so a re-quote of a declined purchase is "another" only
when something else was bought. Covers the compiler flag (both paths), both ledgers, the
signal, decide's order, the pipeline on the public pack, and ``usage.fulfilment``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

from oneguard.api import policies
from oneguard.compiler import compile_instruction
from oneguard.compiler.draft import names_one_item
from oneguard.compiler.parser import parse
from oneguard.engine.decide import decide
from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import InMemoryLedger, Ledger, LedgerEntry
from oneguard.engine.protections import FULFILMENT_UNKNOWN
from oneguard.engine.types import EvidenceRow, Fulfilment, Policy
from oneguard.pipeline import PipelineContext, decide_event
from oneguard.replay.events import Pack, build_events
from oneguard.store import seed as seed_module
from oneguard.store.db import init_db, make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import Run
from tests.test_protections import facts, line, policy, prior, run, signal, view

LENS = "Buy the camera lens I chose, from a seller I have bought from before, for CHF 900 or less. " \
       "Do not add anything I did not ask for. Ask me when uncertain."
RECORDED = Path(__file__).parent / "fixtures" / "compiler" / "judging_responses.yaml"
POLICIES = Path(__file__).parent / "fixtures" / "policies"
T0 = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# --- the compiler: which instructions ask for one item ----------------------------------


@pytest.mark.parametrize(("instruction", "item", "one"), [
    (LENS, "camera lens", True),
    ("Buy the 27-inch monitor I chose, from a seller I have bought from before", "27-inch monitor", True),
    ("Buy the running shoes we picked", "running shoes", True),
    ("Buy one ordinary grocery item for CHF 20 or less", "ordinary grocery item", True),
    ("Buy a bag under CHF 50", "bag", True),
    ("Order an umbrella", "umbrella", True),
    ("Buy a pair of trail shoes", "trail shoes", True),
    ("Buy two concert tickets, max CHF 90 each", "concert tickets", False),
    ("Renew my gym membership, same price as last time", "gym membership", False),
    ("I need new hiking boots, size 42", "hiking boots", False),
    ("Replace my worn road-running shoes in size 43", "road-running shoes", False),
    ("Buy the running shoes", "running shoes", False),
    ("Buy a hotel room", None, False),
])
def test_one_item_is_read_from_the_customers_words(instruction, item, one):
    assert names_one_item(instruction, item) is one


class Recorded:
    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        return self.response


def test_both_compiler_paths_mark_the_chosen_lens_as_one_item():
    history = StoreHistoryIndex(rows=[])
    assert parse(LENS, history, "").single_item
    response = next(e["response"] for e in yaml.safe_load(RECORDED.read_text()) if e["scenario"] == "SCEN0122")
    draft = compile_instruction(LENS, history, "", Recorded(response))
    assert draft.compiler == "llm" and draft.requested_item == "camera lens" and draft.single_item


def test_the_flag_is_stored_with_the_policy_and_old_policies_read_as_not_stated():
    flags = policies.flags_of(parse(LENS))
    assert flags["single_item"] is True
    _, loaded = policies.load_rules(policies.store_rules([], flags), [])
    assert loaded["single_item"] is True
    _, old = policies.load_rules({policies.POLICY_KEY: {"requested_item": "lens"}}, [])
    assert old["single_item"] is False


# --- the signal ---------------------------------------------------------------------------


def one_lens(**kw) -> Policy:
    return policy(requested_item="27-inch monitor", single_item=True, **kw)


def bought(at=T0 - timedelta(days=2), amount=289.0, mandate="TM", auth="LIVE-1") -> Fulfilment:
    return Fulfilment(authorization_id=auth, mandate_id=mandate, timestamp=at, billing_amount_chf=amount,
                      item="27-inch monitor")


def fulfilled_view(*done, **kw):
    return view(**kw).model_copy(update={"fulfilments": list(done)})


def test_a8_asks_when_the_one_item_was_bought_and_names_when_and_for_how_much():
    a8 = signal(run(p=one_lens(), v=fulfilled_view(bought())), "A8")
    assert a8.triggered and a8.outcome_if_triggered == "ask" and a8.source == "ledger"
    assert a8.detail == "You already bought the 27-inch monitor on 10 Aug for CHF 289.00; approve another?"


def test_a8_names_the_latest_purchase():
    later = bought(at=T0 - timedelta(days=1), amount=300.0, auth="LIVE-2")
    assert "11 Aug for CHF 300.00" in signal(run(p=one_lens(), v=fulfilled_view(bought(), later)), "A8").detail


@pytest.mark.parametrize(("p", "v", "f"), [
    (one_lens(), None, None),  # nothing bought yet
    (policy(requested_item="27-inch monitor"), bought(), None),  # "monitors", not one
    (one_lens(), bought(mandate="TM_OLD"), None),  # bought under another instruction
    (one_lens(), bought(auth="LIVE-9"), None),  # this very purchase (redelivery)
    (one_lens(), bought(), facts(items=[line(name="USB-C cable")])),  # not the item
], ids=["first", "not-single", "other-mandate", "itself", "other-item"])
def test_a8_does_not_ask(p, v, f):
    assert not signal(run(f, p=p, v=fulfilled_view(*([v] if v else []))), "A8").triggered


def test_a8_with_no_ledger_answer_is_never_a_pass():
    a8 = signal(run(p=one_lens(), v=view()), "A8")  # fulfilments None: not tracked
    assert a8.triggered and a8.detail == FULFILMENT_UNKNOWN
    engine = decide([], [a8], [], [], one_lens(), view())
    assert engine.reason_codes == ["unevaluable"]


def _step1():
    from oneguard.engine.policy import step1_results

    return step1_results(facts(), one_lens())


def test_decide_asks_already_fulfilled_at_step_5():
    engine = decide(_step1(), run(p=one_lens(), v=fulfilled_view(bought())), [], [], one_lens(), view())
    assert (engine.outcome, engine.step, engine.reason_codes, engine.deciding_ids) == (
        "step_up", 5, ["already_fulfilled"], ["A8"])


def test_another_ask_is_named_first_and_a8_stays_in_the_evidence():
    from oneguard.engine.explain import explain

    v = fulfilled_view(bought(), priors=[prior()])  # also a repeat of an order 1 h ago (A3)
    signals = run(p=one_lens(), v=v)
    engine = decide(_step1(), signals, [], [], one_lens(), v)
    assert engine.reason_codes == ["duplicate_suspected"]
    rows = explain(engine, facts(), one_lens(), _step1(), signals).evidence
    assert any(r.rule == "Already bought" and r.outcome == "uncertain" for r in rows)


def test_a_broken_rule_still_declines_first():
    from oneguard.engine.types import RuleResult

    failed = [*_step1(), RuleResult(rule_id="C1", outcome="fail", detail="over", source="event")]
    engine = decide(failed, run(p=one_lens(), v=fulfilled_view(bought())), [], [], one_lens(), view())
    assert engine.outcome == "decline"


# --- the ledgers: only final approvals fulfil ---------------------------------------------


def entry(auth: str, outcome: str = "approve", run_id: str = "run-1", mandate: str = "TM",
          at: datetime = T0, amount: float = 289.0) -> LedgerEntry:
    pending = outcome == "step_up"
    return LedgerEntry(
        live_authorization_id=auth, run_id=run_id, mandate_id=mandate, card_id="CA1", customer_id="CU1",
        ts_sim=at, outcome=outcome, final=not pending, uncertain_outcome="pending" if pending else None,
        merchant_id="ME1", item_ids=["IT1"], billing_amount_chf=amount, step=5, deciding_ids=[],
        reason_codes=["TEST"], evidence=[EvidenceRow(rule="C1", outcome="pass", detail="d", source="policy")],
        message="m", engine_version="test", latency_ms=1.0, signals_enabled=False, decided_at=NOW,
        deadline_at=NOW + timedelta(minutes=2) if pending else None,
    )  # fmt: skip


@pytest.fixture(params=["store", "memory"])
def ledger(request, tmp_path: Path) -> Iterator[Ledger]:
    if request.param == "memory":
        yield InMemoryLedger()
        return
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield StoreLedger(s)
    engine.dispose()


def fulfilments(led: Ledger, run_id: str = "run-1", at: datetime = T0 + timedelta(hours=1)):
    return led.view(run_id=run_id, customer_id="CU1", card_id="CA1", at=at, period_days=None).fulfilments


def record(led: Ledger, e: LedgerEntry, marked: bool = True) -> None:
    led.record(e)
    if marked:
        led.mark_requested_item(e.live_authorization_id, "27-inch monitor")


def test_an_approval_of_the_item_fulfils(ledger):
    record(ledger, entry("L1", amount=289.0))
    record(ledger, entry("L2", "decline", at=T0 + timedelta(minutes=5)))
    record(ledger, entry("L3", "approve", at=T0 + timedelta(minutes=9)), marked=False)  # not the item
    assert [(f.authorization_id, f.billing_amount_chf, f.item) for f in fulfilments(ledger)] == [
        ("L1", 289.0, "27-inch monitor")]


def test_a_step_up_fulfils_only_once_the_customer_approves_it(ledger):
    record(ledger, entry("L1", "step_up"))
    assert fulfilments(ledger) == []  # waiting: a reservation, not a purchase
    ledger.resolve("L1", "approve", "customer", NOW)
    assert [f.authorization_id for f in fulfilments(ledger)] == ["L1"]


@pytest.mark.parametrize("answer", [("decline", "customer"), ("decline", "timeout")])
def test_a_declined_or_expired_step_up_fulfils_nothing(ledger, answer):
    record(ledger, entry("L1", "step_up"))
    message = "Expired" if answer[1] == "timeout" else None
    ledger.resolve("L1", *answer, NOW, message=message)
    assert fulfilments(ledger) == []


def test_marking_twice_is_one_mark(ledger):
    record(ledger, entry("L1"))
    ledger.mark_requested_item("L1", "27-inch monitor")
    assert len(fulfilments(ledger)) == 1


def test_later_purchases_only_and_this_run_only(ledger):
    record(ledger, entry("L1", at=T0 + timedelta(hours=2)))
    record(ledger, entry("L2", run_id="run-2"))
    assert fulfilments(ledger) == []  # after this purchase, and in another (replay) run


def test_a_live_run_remembers_what_earlier_live_runs_under_the_mandate_bought(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        led = StoreLedger(s)
        for run_id, mandate, day in (("live-0", "TM_OLD", 0), ("live-1", "TM", 1), ("live-2", "TM", 2)):
            s.add(Run(run_id=run_id, kind="live", mandate_id=mandate, card_id="CA1", state="finished",
                      started_at=NOW + timedelta(days=day)))
        s.commit()
        record(led, entry("L0", run_id="live-0", mandate="TM_OLD"))
        record(led, entry("L1", run_id="live-1"))
        assert [f.authorization_id for f in fulfilments(led, "live-2")] == ["L1"]  # not the old mandate's
    engine.dispose()


# --- the pipeline on the public pack (SCEN0004, "the 27-inch monitor I chose") -----------


@pytest.fixture(scope="module")
def pack_and_history(tmp_path_factory: pytest.TempPathFactory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('fulfil') / 'oneguard.sqlite'}")
    seed_module.run(engine=engine)
    with session(engine) as s:
        history = StoreHistoryIndex.load(s)
    engine.dispose()
    return Pack.load(), history


def _monitor_policy() -> Policy:
    fixture = yaml.safe_load((POLICIES / "SCEN0004.yaml").read_text(encoding="utf-8"))
    fixture.pop("scenario_id")
    return Policy.model_validate({"mandate_id": "TM_FULFIL", **fixture})


def _decide(pack_and_history, keep: list[str], answers: dict[str, str] | None = None) -> dict[str, tuple]:
    pack, history = pack_and_history
    policy = _monitor_policy()
    ledger = InMemoryLedger(history=history)
    ctx = PipelineContext(policy=policy, ledger=ledger, history=history, run_id="fulfil", now=lambda: NOW)
    out = {}
    for event in build_events(pack, "SCEN0004", mandate_id=policy.mandate_id, now=NOW):
        auth = event["authorization"]
        if auth["source_authorization_id"] not in keep:
            continue
        engine, explanation, _ = decide_event(event, ctx)
        out[auth["source_authorization_id"]] = (engine.outcome, engine.reason_codes, explanation.message)
        if (answer := (answers or {}).get(auth["source_authorization_id"])) and engine.outcome == "step_up":
            ledger.resolve(auth["authorization_id"], answer, "customer", NOW)
    return out


def test_the_second_monitor_asks_with_the_first_ones_date_and_price(pack_and_history):
    got = _decide(pack_and_history, ["AU0035", "AU0045"])
    assert got["AU0035"][0] == "approve"
    assert got["AU0045"] == ("step_up", ["already_fulfilled"], (
        "Waiting for you CHF 399.90: You already bought the 27-inch monitor on 12 Aug for CHF 289.00; "
        "approve another?"))


def test_a_requote_of_a_declined_purchase_is_not_another(pack_and_history):
    """AU0037 is declined (over the limit, injected text); its re-quote AU0042 is then the
    first monitor bought, so it approves; the next monitor asks."""
    got = _decide(pack_and_history, ["AU0037", "AU0042", "AU0045"])
    assert got["AU0037"][0] == "decline"
    assert got["AU0042"][0] == "approve" and "requote_accepted" in got["AU0042"][1]
    assert got["AU0045"][:2] == ("step_up", ["already_fulfilled"])


def test_the_customers_yes_to_another_counts_it_as_bought(pack_and_history):
    got = _decide(pack_and_history, ["AU0035", "AU0038", "AU0045"], answers={"AU0038": "approve"})
    assert "12 Aug for CHF 289.00" in got["AU0038"][2]
    assert got["AU0045"][0] == "step_up" and "for CHF 391.50" in got["AU0045"][2]


# --- usage.fulfilment -----------------------------------------------------------------------


def test_usage_counts_what_was_bought_of_the_one_item():
    entries = [entry("L1"), entry("L2", "decline"), entry("L3", "step_up"), entry("L4")]
    flags = {"single_item": True, "requested_item": "27-inch monitor"}
    marked = {"L1": "27-inch monitor", "L2": "27-inch monitor", "L3": "27-inch monitor", "L4": "camera lens"}
    got = policies.fulfilment(flags, entries, marked)
    assert (got.bought, got.requested) == (1, 1)  # L4 bought another mandate's item
    assert policies.fulfilment({"single_item": False, "requested_item": "lens"}, entries, marked) is None
    usage = policies.usage([], entries, T0, got)
    assert usage.fulfilment == got
