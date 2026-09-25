"""Canonical JSON: the exact bytes OneGuard signs (docs/passport.md, "Canonical JSON").

Anyone holding a passport or receipt can rebuild these bytes and check the signature
against OneGuard's public key. The rules, applied recursively:

- objects: keys sorted by code point, no whitespace (``{"a":1,"b":2}``);
- strings: UTF-8, non-ASCII characters written as themselves (not ``\\u`` escapes);
- money: a number under a money key (``billing_amount_chf``, ``amount``, ``unit_price``)
  is written as a string with exactly two decimals, rounded half-even (``"520.00"``);
  documents already carry money this way, so a verifier sees the same string;
- other numbers: a number with no fractional part is an integer (``400``, never
  ``400.0``); any other number is the shortest form that reads back as the same value
  (``399.9``), as JSON.stringify writes it;
- timestamps: a ``datetime`` is written as ISO 8601 UTC with ``Z`` and whole seconds
  (``"2026-09-25T10:00:00Z"``); documents already carry timestamps this way;
- ``true`` / ``false`` / ``null`` as JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

MONEY_KEYS = frozenset({"billing_amount_chf", "amount", "unit_price"})
_CENT = Decimal("0.01")


def money(value: Any) -> str:
    """An amount as the fixed two-decimal string documents carry (half-even)."""
    return f"{Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_EVEN):.2f}"


def timestamp(value: datetime) -> str:
    """ISO 8601 UTC with ``Z``, whole seconds."""
    if value.tzinfo is None:
        raise ValueError("naive datetime; documents carry UTC instants only")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: float | Decimal) -> int | float:
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("documents carry finite numbers only")
        if value.is_integer():
            return int(value)
    return value


def normalise(obj: Any, key: str | None = None) -> Any:
    """``obj`` as plain JSON values under the rules above (what ``canonical`` serialises)."""
    if isinstance(obj, dict):
        return {str(k): normalise(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [normalise(v, key) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int | float | Decimal):
        return money(obj) if key in MONEY_KEYS else _number(obj)
    if isinstance(obj, datetime):
        return timestamp(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"{type(obj).__name__} is not a document value")


def canonical(obj: Any) -> bytes:
    """The canonical UTF-8 bytes of ``obj`` (the same object always gives the same bytes)."""
    return json.dumps(
        normalise(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def as_signed(document: Any) -> Any:
    """``document`` with its keys in canonical order: served as it was signed (a store such as
    Postgres ``jsonb`` hands keys back in its own order)."""
    return json.loads(canonical(document))


def sha256_hex(obj: Any) -> str:
    """``sha256(canonical(obj))`` as lowercase hex (``items_hash``, ``evidence_hash``)."""
    return hashlib.sha256(canonical(obj)).hexdigest()
