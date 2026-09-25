"""Demo reset: clear the public cards and the replays, keep the judging runs (operator only).

    python scripts/demo_reset.py              # dry run (the default): what would go, per table
    python scripts/demo_reset.py --apply      # dump every row it deletes as JSON, then delete

Connects with ``ONEGUARD_DATABASE_URL``. Everything happens in one transaction; nothing is
committed unless every delete removed exactly the rows the plan counted.

Keeps: the reference tables, ``worker_state`` (the events cursor, the served scenarios),
``signing_keys``, ``scenario_profiles``, and every live judging run (``SCEN01xx``) with its
decisions, events, receipts, mandates and passports.

Deletes:
- every replay run, with its decisions, events, receipts, requested-item marks
  (``requested_item_orders``) and shop flags (``merchant_flags``);
- every draft, mandate and passport on the public cards (``PUBLIC_CARDS``) and their devices;
- every device nonce, and the whole ``viseca_calls`` table.

It refuses (and changes nothing) when a kept row would point at a deleted one, a run is
neither a replay nor a live judging run, or a judging run sits on a public card.

Nothing on the Viseca platform is touched: revoking the public mandates still active there
is a separate step (docs/demo-script.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Table, delete, func, select, text

from oneguard.store.db import make_engine
from oneguard.store.schema import Base

PUBLIC_CARDS = ("CA0001", "CA0002", "CA0011", "CA0023", "CA0038", "CA0039")
JUDGING = re.compile(r"^SCEN01\d\d$")
DUMP_DIR = Path.home() / "firstmate" / "data" / "oneguard-cleanup"
T = Base.metadata.tables


class Refused(Exception):
    """The store holds something the reset does not expect: nothing is changed."""


@dataclass
class Plan:
    """Per table, the primary keys to delete (``everything`` deletes the whole table)."""

    keys: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)
    everything: set[str] = field(default_factory=set)
    kept_runs: list[tuple[str, str]] = field(default_factory=list)  # (run_id, scenario_id)

    def count(self, table: str) -> int:
        return len(self.keys.get(table, []))


def _pk(table: Table) -> list[Any]:
    return list(table.primary_key.columns)


def _keys(conn: Connection, table: str, where: Any) -> list[tuple[Any, ...]]:
    t = T[table]
    return [tuple(r) for r in conn.execute(select(*_pk(t)).where(where).order_by(*_pk(t)))]


def plan(conn: Connection) -> Plan:
    """What the reset deletes, checked against what it keeps."""
    runs, decisions, events = T["runs"], T["decisions"], T["events_raw"]
    receipts, mandates, passports = T["receipts"], T["mandates"], T["passports"]
    out = Plan()

    all_runs = list(conn.execute(select(runs.c.run_id, runs.c.kind, runs.c.scenario_id, runs.c.card_id,
                                        runs.c.mandate_id)))  # fmt: skip
    replay = [r.run_id for r in all_runs if r.kind == "replay"]
    kept = [r for r in all_runs if r.kind != "replay"]
    odd = [r for r in kept if r.kind != "live" or not JUDGING.match(r.scenario_id or "")]
    if odd:
        raise Refused(f"runs that are neither replays nor live judging runs: {[(r.run_id, r.kind, r.scenario_id) for r in odd]}")
    on_public = [r.run_id for r in kept if r.card_id in PUBLIC_CARDS]
    if on_public:
        raise Refused(f"judging runs on a public card: {on_public}")
    out.kept_runs = sorted((r.run_id, r.scenario_id) for r in kept)

    out.keys["runs"] = _keys(conn, "runs", runs.c.kind == "replay")
    out.keys["decisions"] = _keys(conn, "decisions", decisions.c.run_id.in_(replay))
    gone_live = [k[0] for k in out.keys["decisions"]]
    out.keys["events_raw"] = _keys(conn, "events_raw", events.c.run_id.in_(replay))
    out.keys["receipts"] = _keys(conn, "receipts", receipts.c.live_authorization_id.in_(gone_live))
    out.keys["requested_item_orders"] = _keys(
        conn, "requested_item_orders", T["requested_item_orders"].c.live_authorization_id.in_(gone_live))
    out.keys["merchant_flags"] = _keys(conn, "merchant_flags", T["merchant_flags"].c.run_id.in_(replay))

    out.keys["policy_drafts"] = _keys(conn, "policy_drafts", T["policy_drafts"].c.card_id.in_(PUBLIC_CARDS))
    out.keys["mandates"] = _keys(conn, "mandates", mandates.c.card_id.in_(PUBLIC_CARDS))
    gone_mandates = [k[0] for k in out.keys["mandates"]]
    out.keys["passports"] = _keys(
        conn, "passports", passports.c.card_id.in_(PUBLIC_CARDS) | passports.c.mandate_id.in_(gone_mandates))
    gone_passports = sorted({k[0] for k in out.keys["passports"]})
    out.keys["devices"] = _keys(conn, "devices", T["devices"].c.card_id.in_(PUBLIC_CARDS))
    out.everything = {"device_nonces", "viseca_calls"}

    # Nothing kept may point at what goes.
    kept_ids = [r.run_id for r in kept]
    dangling = {
        "kept runs on a deleted mandate": conn.execute(
            select(runs.c.run_id).where(runs.c.run_id.in_(kept_ids), runs.c.mandate_id.in_(gone_mandates))).all(),
        "kept decisions on a deleted mandate or a public card": conn.execute(
            select(decisions.c.live_authorization_id).where(
                decisions.c.run_id.not_in(replay),
                decisions.c.mandate_id.in_(gone_mandates) | decisions.c.card_id.in_(PUBLIC_CARDS))).all(),
        "kept receipts on a deleted passport": conn.execute(
            select(receipts.c.receipt_id).where(
                receipts.c.live_authorization_id.not_in(gone_live), receipts.c.passport_id.in_(gone_passports))).all(),
        "kept mandates holding a deleted passport": conn.execute(
            select(mandates.c.mandate_id).where(
                mandates.c.mandate_id.not_in(gone_mandates), mandates.c.passport_id.in_(gone_passports))).all(),
    }  # fmt: skip
    found = {what: [r[0] for r in rows] for what, rows in dangling.items() if rows}
    if found:
        raise Refused(f"kept rows would point at deleted ones: {found}")
    return out


def _totals(conn: Connection, tables: Sequence[str]) -> dict[str, int]:
    return {t: conn.execute(select(func.count()).select_from(T[t])).scalar_one() for t in tables}


def _json(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def _rows(conn: Connection, table: str, keys: list[tuple[Any, ...]] | None) -> list[dict[str, Any]]:
    t = T[table]
    if keys is None:
        return [dict(r._mapping) for r in conn.execute(select(t))]
    pk = _pk(t)
    wanted = set(keys)
    return [dict(r._mapping) for r in conn.execute(select(t).order_by(*pk)) if tuple(r._mapping[c.name] for c in pk) in wanted]


def _delete(conn: Connection, table: str, keys: list[tuple[Any, ...]] | None) -> int:
    t = T[table]
    if keys is None:
        return conn.execute(delete(t)).rowcount
    pk = _pk(t)
    removed = 0
    for key in keys:
        removed += conn.execute(delete(t).where(*[c == v for c, v in zip(pk, key, strict=True)])).rowcount
    return removed


def run(url: str, *, apply: bool, dump_dir: Path = DUMP_DIR, out: Callable[[str], None] = print) -> dict[str, int]:
    """The per-table counts deleted (or, on a dry run, that would be)."""
    engine = make_engine(url, pooled=False)
    try:
        with engine.begin() as conn:
            if not apply and engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))  # a dry run cannot write, whatever happens
            p = plan(conn)
            tables = [*p.keys, *sorted(p.everything)]
            before = _totals(conn, tables)
            counts = {t: p.count(t) for t in p.keys} | {t: before[t] for t in p.everything}
            out(f"kept: {len(p.kept_runs)} live judging runs over "
                f"{len({s for _, s in p.kept_runs})} scenarios ({', '.join(sorted({s for _, s in p.kept_runs}))})")
            for t in tables:
                out(f"{t}: delete {counts[t]} of {before[t]}")
            if not apply:
                out("dry run: nothing changed (--apply to delete)")
                conn.rollback()
                return counts
            dump_dir.mkdir(parents=True, exist_ok=True)
            for t in tables:
                rows = _rows(conn, t, None if t in p.everything else p.keys[t])
                if len(rows) != counts[t]:
                    raise Refused(f"{t}: read {len(rows)} rows to dump, planned {counts[t]}")
                path = dump_dir / f"demo-reset-{t}.json"
                path.write_text(json.dumps(rows, default=_json, indent=1, sort_keys=True), encoding="utf-8")
                out(f"dumped {len(rows)} {t} rows to {path}")
            for t in tables:
                removed = _delete(conn, t, None if t in p.everything else p.keys[t])
                if removed != counts[t]:
                    raise Refused(f"{t}: deleted {removed}, planned {counts[t]}; rolled back")
            after = _totals(conn, tables)
            wrong = {t: (before[t], counts[t], after[t]) for t in tables if after[t] != before[t] - counts[t]}
            if wrong:
                raise Refused(f"counts after delete do not add up (before, deleted, after): {wrong}; rolled back")
            out("committed")
            return counts
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="count only (the default)")
    mode.add_argument("--apply", action="store_true", help="dump the rows, then delete them in one transaction")
    parser.add_argument("--dump-dir", type=Path, default=DUMP_DIR)
    args = parser.parse_args(argv)
    url = os.environ.get("ONEGUARD_DATABASE_URL", "").strip()
    if not url:
        print("ONEGUARD_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    try:
        run(url, apply=args.apply, dump_dir=args.dump_dir)
    except Refused as exc:
        print(f"refused, nothing changed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
