"""Shared guards for concrete providers (P4): parse, validate, bound the wall clock.

Every provider returns only JSON that validates against the caller's schema
(provider.py contract); anything else is ProviderUnavailable, so the caller falls back
to its deterministic path (rules.md P8).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import jsonschema

from oneguard.llm.provider import ProviderUnavailable

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="oneguard-llm")


def parse_and_validate(raw: str | dict[str, Any] | None, schema: dict[str, Any]) -> dict[str, Any]:
    """Decode ``raw`` (text or an already-decoded object) and validate it against ``schema``."""
    if raw is None:
        raise ProviderUnavailable("model returned no content")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ProviderUnavailable(f"model returned invalid JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise ProviderUnavailable("model returned JSON that is not an object")
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise ProviderUnavailable(f"model output failed the schema: {exc.message}") from None
    return data


def with_deadline[T](fn: Callable[[], T], timeout_s: float) -> T:
    """Run ``fn`` and give up after ``timeout_s`` of wall clock (the SDK timeouts bound
    each read, not the whole call). A late answer is discarded, never used."""
    future = _POOL.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout:
        future.cancel()
        raise ProviderUnavailable(f"model did not answer within {timeout_s:g} s") from None
