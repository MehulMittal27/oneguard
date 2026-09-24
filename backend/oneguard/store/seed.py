"""Load the challenge CSVs into the reference tables (docs/database.md §1, §4).

Idempotent: each run replaces the reference tables' contents in one transaction.
Before loading, every file listed in ``data/metadata.json`` is checked against its
SHA-256 (and CSV row count) so a changed pack is noticed.

``reseed_history`` replaces only ``authorization_history`` from CSV text, for the Viseca
worker when the platform serves a history file whose hash differs from the pack's.

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
from datetime import date, datetime
from decimal import Decimal
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
)
from sqlalchemy.orm import Session

from oneguard.store.db import database_url, drop_db, get_engine, init_db, session
from oneguard.store.history import normalise_merchant_name
from oneguard.store.schema import (
    REFERENCE_TABLES,
    AuthorizationHistory,
    Merchant,
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
