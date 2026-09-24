"""Validation of live events against data/schemas/authorization_event.schema.json.

The worker validates the envelope's ``data`` before anything reads it
(technical_details.md "Prepare your worker"). Format keywords (date-time, date) are
checked where jsonschema supports them.

Tolerant of additions: the schema closes every object (``additionalProperties: false``),
but a property it does not list is not a reason to decline. ``check_event`` validates
against the schema with those closures lifted, so required fields, types, enums and
formats still hold, and names the extra properties separately: the worker logs them and
the event is passed on unchanged (nothing reads them). A missing required field is still
an error, named by its path (``authorization.merchant is missing``).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from oneguard.store.seed import DATA_DIR

EVENT_SCHEMA_PATH = DATA_DIR / "schemas" / "authorization_event.schema.json"


@dataclass(frozen=True)
class EventCheck:
    """``errors``: why the event cannot be decided (empty when it can). ``extras``: the
    properties the schema does not list, as dotted paths (``authorization.loyalty_points``)."""

    errors: list[str]
    extras: list[str]


def _open(schema: Any) -> Any:
    """``schema`` with every ``additionalProperties: false`` removed."""
    if isinstance(schema, dict):
        return {
            k: _open(v) for k, v in schema.items() if not (k == "additionalProperties" and v is False)
        }
    if isinstance(schema, list):
        return [_open(v) for v in schema]
    return schema


@cache
def _validators(path: Path = EVENT_SCHEMA_PATH) -> tuple[Draft202012Validator, Draft202012Validator]:
    """(strict, tolerant): the schema as published, and the same with objects left open."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    tolerant = _open(copy.deepcopy(schema))
    return (
        Draft202012Validator(schema, format_checker=FormatChecker()),
        Draft202012Validator(tolerant, format_checker=FormatChecker()),
    )


def event_validator(path: Path = EVENT_SCHEMA_PATH) -> Draft202012Validator:
    """The schema as published (every object closed)."""
    return _validators(path)[0]


def _where(error: ValidationError) -> str:
    return ".".join(map(str, error.absolute_path))


def _describe(error: ValidationError) -> str:
    where = _where(error)
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [f for f in error.validator_value if f not in error.instance]
        if len(missing) == 1:
            return f"{'.'.join(filter(None, [where, missing[0]]))} is missing"
    return f"{where or '<root>'}: {error.message}"


def _extra_paths(error: ValidationError) -> list[str]:
    listed = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
    where = _where(error)
    instance = error.instance if isinstance(error.instance, dict) else {}
    return [".".join(filter(None, [where, str(k)])) for k in instance if k not in listed]


def check_event(data: Any) -> EventCheck:
    """Validate ``data``: errors against the open schema, extras against the closed one."""
    strict, tolerant = _validators()
    errors = sorted(tolerant.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    extras = sorted(
        {p for e in strict.iter_errors(data) if e.validator == "additionalProperties" for p in _extra_paths(e)}
    )
    return EventCheck(errors=[_describe(e) for e in errors], extras=extras)


def event_errors(data: Any) -> list[str]:
    """Every reason ``data`` cannot be decided; empty when it can (extras are not errors)."""
    return check_event(data).errors
