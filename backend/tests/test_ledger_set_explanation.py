"""``Ledger.set_explanation`` (added by P1 for tier 3 in the Viseca worker; P2 to review).

After a decision is posted, the worker stores tier 3's rewrite of its message. Same
contract on ``StoreLedger`` and ``InMemoryLedger``: ``message`` and
``explanation_source`` change, nothing else; it is committed; any entry may take it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from oneguard.engine.ledger import StoreLedger
from oneguard.engine.ledger_base import Ledger
from oneguard.store.db import init_db, make_engine
from tests.test_ledger_set_deadline import (  # noqa: F401 (fixture)
    DECIDED,
    entry,
    ledger,
)

REWRITE = "I need your OK before paying CHF 42.50 here."
CHANGED = {"message", "explanation_source"}


@pytest.mark.parametrize("outcome", ["approve", "decline", "step_up"])
def test_set_explanation_changes_only_the_message_and_its_source(ledger: Ledger, outcome: str) -> None:  # noqa: F811
    stored = ledger.record(entry("lv_1", outcome=outcome))
    assert stored.explanation_source == "template"
    rewritten = ledger.set_explanation("lv_1", REWRITE)
    assert (rewritten.message, rewritten.explanation_source) == (REWRITE, "model")
    assert rewritten.model_dump(exclude=CHANGED) == stored.model_dump(exclude=CHANGED)
    assert ledger.get("lv_1") == rewritten


def test_set_explanation_keeps_a_resolved_step_up_resolved(ledger: Ledger) -> None:  # noqa: F811
    ledger.record(entry("lv_1"))
    resolved = ledger.resolve("lv_1", "approve", "customer", DECIDED + timedelta(seconds=30))
    rewritten = ledger.set_explanation("lv_1", REWRITE)
    assert rewritten.model_dump(exclude=CHANGED) == resolved.model_dump(exclude=CHANGED)
    assert rewritten.spent_chf == 42.5 and rewritten.uncertain_outcome == "approved"


def test_set_explanation_refuses_an_unknown_id(ledger: Ledger) -> None:  # noqa: F811
    with pytest.raises(KeyError):
        ledger.set_explanation("lv_unknown", REWRITE)


def test_set_explanation_is_committed(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        StoreLedger(s).record(entry("lv_1"))
        StoreLedger(s).set_explanation("lv_1", REWRITE)
    with Session(engine) as s:
        found = StoreLedger(s).get("lv_1")
    engine.dispose()
    assert found is not None and (found.message, found.explanation_source) == (REWRITE, "model")
