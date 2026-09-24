"""Validation of live events against data/schemas/authorization_event.schema.json.

The worker validates the envelope's ``data`` before anything reads it
(technical_details.md "Prepare your worker"). Format keywords (date-time, date) are
checked where jsonschema supports them.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from oneguard.store.seed import DATA_DIR

EVENT_SCHEMA_PATH = DATA_DIR / "schemas" / "authorization_event.schema.json"


@cache
def event_validator(path: Path = EVENT_SCHEMA_PATH) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def event_errors(data: Any) -> list[str]:
    """Every schema violation in ``data`` as ``path: message``; empty when valid."""
    errors = sorted(event_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
