"""scripts/demo_reset.py on a small SQLite store: the dry run counts and changes nothing;
--apply dumps every deleted row, deletes exactly the plan and keeps the judging runs; a
store it does not expect is refused with nothing changed."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table, func, insert, select

from oneguard.store.db import init_db, make_engine
from oneguard.store.schema import Base

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_reset.py"
spec = importlib.util.spec_from_file_location("demo_reset", SCRIPT)
assert spec and spec.loader
demo_reset = importlib.util.module_from_spec(spec)
sys.modules["demo_reset"] = demo_reset  # its dataclass looks its module up while the script loads
spec.loader.exec_module(demo_reset)

T = Base.metadata.tables
NOW = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
FILL: dict[type, Any] = {str: "x", int: 0, float: 0.0, bool: False, datetime: NOW, Decimal: Decimal(0),
                         dict: {}, list: []}  # fmt: skip


def row(table: Table, **values: Any) -> dict[str, Any]:
    """Every required column filled with a neutral value, then ``values``."""
    out: dict[str, Any] = {}
    for c in table.columns:
        if c.nullable or (c.primary_key and c.autoincrement is True):
            continue
        try:
            out[c.name] = FILL[c.type.python_type]
        except NotImplementedError:  # the store's own types: UTC datetimes, JSON
            out[c.name] = NOW if "DateTime" in type(c.type).__name__ else {}
    return out | values


def seed(url: str) -> None:
    engine = make_engine(url)
    init_db(engine)
    rows: list[tuple[str, dict[str, Any]]] = []

    def run(run_id: str, kind: str, scenario: str, card: str, mandate: str, lives: list[str]) -> None:
        rows.append(("runs", {"run_id": run_id, "kind": kind, "scenario_id": scenario, "card_id": card,
                              "mandate_id": mandate, "state": "done", "started_at": NOW}))  # fmt: skip
        for live in lives:
            rows.append(("decisions", {"live_authorization_id": live, "run_id": run_id, "mandate_id": mandate,
                                       "card_id": card, "receipt_id": f"rc_{live}"}))  # fmt: skip
            rows.append(("events_raw", {"live_authorization_id": live, "run_id": run_id}))
            rows.append(("receipts", {"receipt_id": f"rc_{live}", "live_authorization_id": live,
                                      "passport_id": f"pp_{mandate}", "passport_version": 1}))  # fmt: skip
            rows.append(("requested_item_orders", {"live_authorization_id": live, "item": "lens"}))
        rows.append(("merchant_flags", {"run_id": run_id, "merchant_id": "ME1", "flagged_at": NOW, "reason": "A1"}))

    def mandate(mandate_id: str, card: str, status: str = "active") -> None:
        rows.append(("mandates", {"mandate_id": mandate_id, "card_id": card, "status": status,
                                  "passport_id": f"pp_{mandate_id}"}))  # fmt: skip
        for version in (1, 2):
            rows.append(("passports", {"passport_id": f"pp_{mandate_id}", "version": version,
                                       "mandate_id": mandate_id, "card_id": card}))  # fmt: skip

    mandate("md_judge", "CA1331")  # a judging run's policy, on its served card
    run("run_judge_1", "live", "SCEN0101", "CA1331", "md_judge", ["AU10101-a"])
    run("run_judge_2", "live", "SCEN0101", "CA1331", "md_judge", ["AU10101-b"])
    mandate("md_public", "CA0039")
    mandate("md_public_old", "CA0039", "revoked")
    run("run_replay", "replay", "SCEN0004", "CA0039", "md_public", ["AU0035-r", "AU0036-r"])
    rows.append(("policy_drafts", {"draft_id": "pd_1", "card_id": "CA0039"}))
    rows.append(("policy_drafts", {"draft_id": "pd_2", "card_id": "CA1331"}))
    rows.append(("devices", {"device_id": "dv_phone", "card_id": "CA0039", "status": "enrolled"}))
    rows.append(("devices", {"device_id": "dv_judge", "card_id": "CA1331", "status": "enrolled"}))
    rows.append(("device_nonces", {"device_id": "dv_phone", "nonce": "n1", "seen_at": NOW}))
    rows.append(("viseca_calls", {}))
    rows.append(("worker_state", {"key": "events_cursor", "value": {"cursor": 7}, "updated_at": NOW}))
    rows.append(("signing_keys", {"key_id": "ogk_1"}))
    with engine.begin() as conn:
        for table, values in rows:
            conn.execute(insert(T[table]).values(**row(T[table], **values)))
    engine.dispose()


def counts(url: str) -> dict[str, int]:
    engine = make_engine(url)
    with engine.connect() as conn:
        out = {t: conn.execute(select(func.count()).select_from(T[t])).scalar_one() for t in T}
    engine.dispose()
    return out


EXPECTED = {
    "runs": 1, "decisions": 2, "events_raw": 2, "receipts": 2, "requested_item_orders": 2, "merchant_flags": 1,
    "policy_drafts": 1, "mandates": 2, "passports": 4, "devices": 1, "device_nonces": 1, "viseca_calls": 1,
}  # fmt: skip


@pytest.fixture
def store(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    seed(url)
    return url


def test_the_dry_run_counts_and_changes_nothing(store: str) -> None:
    before = counts(store)
    lines: list[str] = []
    assert demo_reset.run(store, apply=False, out=lines.append) == EXPECTED
    assert counts(store) == before
    assert lines[0] == "kept: 2 live judging runs over 1 scenarios (SCEN0101)"
    assert "passports: delete 4 of 6" in lines and lines[-1].startswith("dry run: nothing changed")


def test_apply_dumps_every_deleted_row_and_keeps_the_judging_runs(store: str, tmp_path: Path) -> None:
    before = counts(store)
    dumps = tmp_path / "dumps"
    assert demo_reset.run(store, apply=True, dump_dir=dumps, out=lambda _: None) == EXPECTED
    after = counts(store)
    assert {t: before[t] - after[t] for t in EXPECTED} == EXPECTED
    assert all(before[t] == after[t] for t in before if t not in EXPECTED)  # reference, cursor, keys untouched
    for table, n in EXPECTED.items():
        dumped = json.loads((dumps / f"demo-reset-{table}.json").read_text(encoding="utf-8"))
        assert len(dumped) == n, table
    engine = make_engine(store)
    with engine.connect() as conn:
        assert [r[0] for r in conn.execute(select(T["runs"].c.run_id).order_by(T["runs"].c.run_id))] == [
            "run_judge_1", "run_judge_2"]
        assert conn.execute(select(T["mandates"].c.mandate_id)).scalars().all() == ["md_judge"]
        assert conn.execute(select(T["devices"].c.device_id)).scalars().all() == ["dv_judge"]
        assert conn.execute(select(T["worker_state"].c.key)).scalars().all() == ["events_cursor"]
    engine.dispose()
    assert demo_reset.run(store, apply=False, out=lambda _: None) == dict.fromkeys(EXPECTED, 0)  # nothing left


@pytest.mark.parametrize("change", ["live-run-elsewhere", "judging-on-public-card", "kept-receipt-on-deleted-passport"])
def test_an_unexpected_store_is_refused_with_nothing_changed(store: str, change: str) -> None:
    engine = make_engine(store)
    with engine.begin() as conn:
        if change == "live-run-elsewhere":
            conn.execute(insert(T["runs"]).values(**row(T["runs"], run_id="run_demo", kind="live",
                         scenario_id="SCEN0001", card_id="CA0001", mandate_id="md_x", started_at=NOW)))  # fmt: skip
        elif change == "judging-on-public-card":
            conn.execute(T["runs"].update().where(T["runs"].c.run_id == "run_judge_2").values(card_id="CA0001"))
        else:
            conn.execute(T["receipts"].update().where(T["receipts"].c.receipt_id == "rc_AU10101-a")
                         .values(passport_id="pp_md_public"))  # fmt: skip
    engine.dispose()
    before = counts(store)
    with pytest.raises(demo_reset.Refused):
        demo_reset.run(store, apply=True, out=lambda _: None)
    assert counts(store) == before
