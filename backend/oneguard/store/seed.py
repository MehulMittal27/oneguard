"""Load the challenge CSVs into the reference tables (docs/database.md §1, §4).

Idempotent: each run replaces the reference tables' contents in one transaction.
Before loading, every file listed in ``data/metadata.json`` is checked against its
SHA-256 (and CSV row count) so a changed pack is noticed.

``reseed_history`` replaces only ``authorization_history`` from CSV text, for the Viseca
worker when the platform serves a history file whose hash differs from the pack's.

``sync_served`` upserts the reference tables ``GET /v1/reference-data`` serves under
``tables`` (a superset of the pack during judging): missing rows are inserted, changed
ones updated, nothing is deleted, and a table whose served rows already match the store
(same count, same content hash) is not touched.

    python -m oneguard.store.seed            # make seed
    python -m oneguard.store.seed --reset    # make reset-db: drop, create, seed

``--reset`` is refused when ``ONEGUARD_ENV`` is ``prod``. Only this module and
``replay/`` open CSVs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Engine,
    Integer,
    Numeric,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session

from oneguard.store.db import database_url, drop_db, get_engine, init_db, session
from oneguard.store.history import normalise_merchant_name
from oneguard.store.schema import (
    REFERENCE_TABLES,
    AuthorizationHistory,
    Base,
    Merchant,
    ScenarioCatalogue,
    UtcDateTime,
)

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
ENV_VAR = "ONEGUARD_ENV"


class PackMismatch(RuntimeError):
    """A file in the data pack does not match ``metadata.json``."""


class ResetRefused(RuntimeError):
    """``reset-db`` was asked for in production."""


def verify_pack(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """Check every file's SHA-256 and CSV row count against ``metadata.json``."""
    metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
    problems = []
    for entry in metadata["files"]:
        path = data_dir / entry["path"]
        if not path.is_file():
            problems.append(f"{entry['path']}: missing")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            problems.append(f"{entry['path']}: sha256 {digest} != {entry['sha256']}")
        if entry.get("format") == "csv" and "rows" in entry:
            with path.open(newline="", encoding="utf-8") as f:
                rows = sum(1 for _ in csv.DictReader(f))
            if rows != entry["rows"]:
                problems.append(f"{entry['path']}: {rows} rows != {entry['rows']}")
    if problems:
        raise PackMismatch("data pack differs from metadata.json: " + "; ".join(problems))
    return metadata


def _coerce(column: Any, raw: str) -> Any:
    if raw == "" and column.nullable:
        return None
    kind = column.type
    if isinstance(kind, UtcDateTime):
        return datetime.fromisoformat(raw)
    if isinstance(kind, Boolean):
        if raw not in ("true", "false"):
            raise ValueError(f"{column.name}: expected true/false, got {raw!r}")
        return raw == "true"
    if isinstance(kind, Numeric):
        return Decimal(raw)
    if isinstance(kind, Integer):
        return int(raw)
    if isinstance(kind, Date):
        return date.fromisoformat(raw)
    return raw


def _parse_rows(model: type, f: Any, name: str) -> list[dict[str, Any]]:
    columns = {c.name: c for c in model.__table__.columns}
    reader = csv.DictReader(f)
    unexpected = set(reader.fieldnames or ()) - set(columns)
    if unexpected:
        raise PackMismatch(f"{name}: unexpected columns {sorted(unexpected)}")
    return [{k: _coerce(columns[k], v) for k, v in raw.items()} for raw in reader]


def _read_table(model: type, data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / f"{model.__table__.name}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        rows = _parse_rows(model, f, path.name)
    if model is Merchant:
        for row in rows:
            row["name_normalised"] = normalise_merchant_name(row["merchant_name"])
    return rows


def seed(s: Session, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Replace every reference table's rows with the pack's; returns rows per table."""
    verify_pack(data_dir)
    loaded = {model: _read_table(model, data_dir) for model in REFERENCE_TABLES}
    for model in reversed(REFERENCE_TABLES):
        s.execute(delete(model))
    counts = {}
    for model in REFERENCE_TABLES:
        rows = loaded[model]
        if rows:
            # Core insert on the Table: one executemany per table, which psycopg pipelines.
            # The ORM bulk path splits rows wherever their NULL columns change, and each
            # batch costs a round trip to the Supabase pooler (~2 min instead of seconds).
            s.execute(insert(model.__table__), rows)
        counts[model.__tablename__] = len(rows)
    s.flush()
    return counts


def pack_file_sha256(path: str, data_dir: Path = DATA_DIR) -> str | None:
    """The SHA-256 ``metadata.json`` records for ``path`` (the seed's stored hash)."""
    metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
    for entry in metadata["files"]:
        if entry["path"] == path:
            return entry["sha256"]
    return None


def reseed_history(s: Session, csv_text: str) -> int:
    """Replace every ``authorization_history`` row with ``csv_text``; returns the row count.

    Runs inside the caller's transaction, so a bad file (unknown column, bad value, a
    foreign key the other reference tables do not have) leaves the old rows in place.
    """
    rows = _parse_rows(AuthorizationHistory, io.StringIO(csv_text, newline=""), "authorization-history.csv")
    if not rows:
        raise PackMismatch("authorization-history.csv: no rows")
    s.execute(delete(AuthorizationHistory))
    s.execute(insert(AuthorizationHistory.__table__), rows)  # Core executemany, as in seed()
    s.flush()
    return len(rows)


# Served reference tables ---------------------------------------------------------------

SERVED_TABLES: dict[str, type[Base]] = {
    m.__tablename__: m for m in REFERENCE_TABLES if m is not AuthorizationHistory
}
"""Tables ``/v1/reference-data`` may serve under ``tables``, in foreign-key order. The
history file is served separately and checked by the worker (``reseed_history``)."""

SERVED_DEFAULTS: dict[type[Base], dict[str, str]] = {
    ScenarioCatalogue: {"control_question": "", "control_theme": "", "short_rationale": ""},
}
"""Values for required columns the served rows do not carry, used only when a row is
inserted (an existing row keeps its own). The served catalogue has only ``scenario_id``,
``scenario_name``, ``cardholder_instruction`` and ``event_count``; the three text columns
are operator notes the store requires, so a served-only scenario gets empty text."""


@dataclass(frozen=True)
class TableSync:
    """What ``sync_served`` did to one table."""

    table: str
    served: int
    before: int
    after: int
    inserted: int
    updated: int
    served_sha256: str

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated)


def _served_value(column: Any, value: Any) -> Any:
    """A served JSON value typed for ``column``: strings as in the CSVs, or JSON scalars."""
    if value is None:
        if column.nullable:
            return None
        raise ValueError("null in a required column")
    if isinstance(value, str):
        return _coerce(column, value)
    kind = column.type
    if isinstance(kind, Boolean) and isinstance(value, bool):
        return value
    if isinstance(kind, Integer) and isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(kind, Numeric) and isinstance(value, int | float) and not isinstance(value, bool):
        return Decimal(str(value))
    raise TypeError(f"unexpected {type(value).__name__} {value!r}")


def _canonical(value: Any) -> Any:
    """``value`` in one form whichever side it came from (served text or the store)."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _key(model: type[Base], row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row[c.name] for c in model.__table__.primary_key.columns)


def _fingerprint(rows: list[dict[str, Any]], columns: list[str]) -> tuple[int, str]:
    """Row count and SHA-256 of ``rows`` restricted to ``columns``, in a stable order."""
    canon = sorted(
        json.dumps([_canonical(row.get(c)) for c in columns], separators=(",", ":"), default=str)
        for row in rows
    )
    return len(canon), hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()


def _served_rows(model: type[Base], raw_rows: Any) -> tuple[list[dict[str, Any]], set[str]]:
    """The served rows typed for ``model``, and the served columns the table does not have."""
    name = model.__tablename__
    if not isinstance(raw_rows, list) or not all(isinstance(r, dict) for r in raw_rows):
        raise PackMismatch(f"served {name}: expected a list of rows")
    columns = {c.name: c for c in model.__table__.columns}
    unknown: set[str] = set()
    rows = []
    for raw in raw_rows:
        unknown |= set(raw) - set(columns)
        try:
            row = {k: _served_value(columns[k], v) for k, v in raw.items() if k in columns}
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise PackMismatch(f"served {name}: bad value in {raw!r}: {exc}") from None
        if any(row.get(c.name) in (None, "") for c in model.__table__.primary_key.columns):
            raise PackMismatch(f"served {name}: a row without its id: {raw!r}")
        if model is Merchant and "merchant_name" in row:
            row["name_normalised"] = normalise_merchant_name(row["merchant_name"])
        rows.append(row)
    if len({_key(model, r) for r in rows}) != len(rows):
        raise PackMismatch(f"served {name}: duplicate ids")
    return rows, unknown


def _complete(model: type[Base], row: dict[str, Any]) -> dict[str, Any]:
    """``row`` ready to insert: served-shape gaps filled, every required column present."""
    full = {**SERVED_DEFAULTS.get(model, {}), **row}
    missing = [c.name for c in model.__table__.columns if c.name not in full and not c.nullable]
    if missing:
        raise PackMismatch(f"served {model.__tablename__} {_key(model, row)}: no {', '.join(missing)}")
    return full


def _sync_table(s: Session, model: type[Base], raw_rows: Any) -> TableSync:
    table = model.__table__
    rows, unknown = _served_rows(model, raw_rows)
    if unknown:
        log.warning("served %s has columns the store does not keep: %s", table.name, sorted(unknown))
    columns = sorted({c for row in rows for c in row})
    stored = {_key(model, r): dict(r) for r in s.execute(select(table)).mappings()}
    before = len(stored)
    served_count, served_sha = _fingerprint(rows, columns)
    same_ids = [stored[k] for k in (_key(model, r) for r in rows) if k in stored]
    if (served_count, served_sha) == _fingerprint(same_ids, columns):
        return TableSync(table.name, served_count, before, before, 0, 0, served_sha)
    inserts, updates = [], []
    for row in rows:
        old = stored.get(_key(model, row))
        if old is None:
            inserts.append(_complete(model, row))
        elif any(_canonical(old[c]) != _canonical(v) for c, v in row.items()):
            updates.append(row)
    if inserts:
        s.execute(insert(table), inserts)  # Core executemany, as in seed()
    pk = list(table.primary_key.columns)
    for row in updates:
        s.execute(
            update(table)
            .where(*(c == row[c.name] for c in pk))
            .values({k: v for k, v in row.items() if k not in {c.name for c in pk}})
        )
    return TableSync(
        table.name, served_count, before, before + len(inserts), len(inserts), len(updates), served_sha
    )


def sync_served(s: Session, tables: Mapping[str, Any]) -> list[TableSync]:
    """Upsert every reference table ``tables`` serves (``/v1/reference-data`` ``tables``).

    Runs inside the caller's transaction, in foreign-key order: a bad row or a foreign key
    the store does not have raises and leaves every table as it was. Rows the store has
    and the served set lacks are kept; runtime tables are never touched. Tables served
    under a name the store does not keep are ignored.
    """
    results = []
    for name, model in SERVED_TABLES.items():
        if name in tables:
            results.append(_sync_table(s, model, tables[name]))
    s.flush()
    return results


def history_row_count(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(AuthorizationHistory)) or 0


def run(reset: bool = False, engine: Engine | None = None, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """``make seed`` / ``make reset-db`` against ``engine`` (default: ONEGUARD_DATABASE_URL)."""
    if reset and os.environ.get(ENV_VAR, "").strip().lower() == "prod":
        raise ResetRefused(f"reset-db refused: {ENV_VAR}=prod")
    engine = engine or get_engine()
    if reset:
        drop_db(engine)
    init_db(engine)
    with session(engine) as s:
        return seed(s, data_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="drop and recreate every table first")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        counts = run(reset=args.reset)
    except (ResetRefused, PackMismatch) as exc:
        log.error("%s", exc)
        return 1
    backend = database_url().split(":", 1)[0]
    for table, n in counts.items():
        log.info("%-24s %6d rows", table, n)
    log.info("seeded %s%s", backend, " after reset" if args.reset else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
