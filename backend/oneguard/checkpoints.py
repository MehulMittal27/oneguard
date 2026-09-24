"""The checkpoint log: every step the engine took on one purchase, with its time.

``decide_event`` fills a ``Recorder`` as it runs: one row per stage and per check, in
the order the engine ran them (rules.md §4), including protections and warning signs
that ran and stayed clear, which the customer's evidence rows leave out. The first row
of each stage carries that stage's wall time. The log is stored once per decision in
``decision_checkpoints`` and returned on ``Decision.checkpoints`` (api-contract §3.10),
the same in offline replay and live runs.

It describes the decision; it never takes part in it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from oneguard.api.models import Checkpoint, CheckpointStage
from oneguard.engine.explain import RULE_LABELS, SIGNAL_LABELS
from oneguard.engine.types import (
    STEP1_RULE_IDS,
    EngineDecision,
    Policy,
    RuleResult,
    Signal,
)
from oneguard.store.schema import DecisionCheckpoints

_RULE_OUTCOME = {"pass": "pass", "fail": "fail", "unknown": "uncertain"}
_SIGNAL_OUTCOME = {"decline": "fail", "ask": "uncertain", "info": "info"}
_STEP_NAMES = {
    1: "policy, permission or card not active",
    2: "a rule you set is broken",
    3: "a protection that declines triggered",
    4: "a fact your rules need is unknown",
    5: "a protection that asks triggered",
    6: "warning signs reached the threshold",
    7: "every check passed",
}
_OUTCOME_WORDS = {"approve": "Approve", "decline": "Decline", "step_up": "Ask you"}


class Recorder:
    """Collects checkpoint rows; ``stage`` times a block and stamps its first row."""

    def __init__(self) -> None:
        self.rows: list[Checkpoint] = []

    @contextmanager
    def stage(self, name: CheckpointStage) -> Iterator[None]:
        """Time the block; its wall time goes on the first row the block added."""
        first = len(self.rows)
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stamp(first, time.perf_counter() - started)

    def stamp(self, index: int, seconds: float) -> None:
        if index < len(self.rows):
            self.rows[index] = self.rows[index].model_copy(update={"ms": round(seconds * 1000, 3)})

    def add(self, stage: CheckpointStage, check: str, outcome: str, detail: str) -> None:
        self.rows.append(Checkpoint(stage=stage, check=check, outcome=outcome, detail=detail))

    def rules(self, results: Iterable[RuleResult], policy: Policy, stage: CheckpointStage = "rules") -> None:
        texts = {r.id: r.text for r in policy.rules}
        for result in results:
            label = texts.get(result.rule_id) or RULE_LABELS.get(result.rule_id, result.rule_id)
            where = "status" if result.rule_id in STEP1_RULE_IDS else stage
            self.add(where, label, _RULE_OUTCOME[result.outcome], result.detail)

    def signals(self, signals: Iterable[Signal], stage: CheckpointStage) -> None:
        for signal in signals:
            label = SIGNAL_LABELS.get(signal.id, signal.id)
            if signal.triggered:
                self.add(stage, label, _SIGNAL_OUTCOME[signal.outcome_if_triggered], signal.detail)
            else:
                self.add(stage, label, "clear", "Checked; nothing found.")

    def decision(self, engine: EngineDecision) -> None:
        reason = _STEP_NAMES.get(engine.step, f"step {engine.step}")
        codes = ", ".join(engine.reason_codes)
        self.add("decide", _OUTCOME_WORDS[engine.outcome], "done", f"Step {engine.step}: {reason} ({codes}).")


def save(session: Session, live_authorization_id: str, rows: list[Checkpoint], at: datetime) -> None:
    """Store the log once; a second write for the same purchase is ignored."""
    if session.get(DecisionCheckpoints, live_authorization_id) is not None:
        return
    session.add(
        DecisionCheckpoints(
            live_authorization_id=live_authorization_id,
            checkpoints=[row.model_dump(mode="json", exclude_none=True) for row in rows],
            recorded_at=at,
        )
    )
    session.commit()


def load(session: Session, live_authorization_ids: Iterable[str]) -> dict[str, list[Checkpoint]]:
    ids = list(dict.fromkeys(live_authorization_ids))
    if not ids:
        return {}
    rows = session.scalars(select(DecisionCheckpoints).where(DecisionCheckpoints.live_authorization_id.in_(ids)))
    return {r.live_authorization_id: [Checkpoint.model_validate(c) for c in r.checkpoints] for r in rows}


def sink(db: Engine) -> Callable[[str, list[Checkpoint], datetime], None]:
    """``PipelineContext.on_checkpoints`` for a store: one short session per decision."""

    def store(live_authorization_id: str, rows: list[Checkpoint], at: datetime) -> None:
        with Session(db, expire_on_commit=False) as s:
            save(s, live_authorization_id, rows, at)

    return store
