"""Load the challenge CSVs into the reference tables (docs/database.md §1, §4).

Idempotent: each run replaces the reference tables' contents in one transaction.
Before loading, every file listed in ``data/metadata.json`` is checked against its
SHA-256 (and CSV row count) so a changed pack is noticed.

    python -m oneguard.store.seed            # make seed
    python -m oneguard.store.seed --reset    # make reset-db: drop, create, seed

``--reset`` is refused when ``ONEGUARD_ENV`` is ``prod``. Only this module and
``replay/`` open CSVs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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


def _read_table(model: type, data_dir: Path) -> list[dict[str, Any]]:
    table = model.__table__
    columns = {c.name: c for c in table.columns}
    path = data_dir / f"{table.name}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        unexpected = set(reader.fieldnames or ()) - set(columns)
        if unexpected:
            raise PackMismatch(f"{path.name}: unexpected columns {sorted(unexpected)}")
        rows = [{k: _coerce(columns[k], v) for k, v in raw.items()} for raw in reader]
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
