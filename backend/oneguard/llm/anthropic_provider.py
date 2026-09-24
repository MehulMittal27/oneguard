"""Anthropic provider (P4): structured outputs via ``output_config.format``.

Working but not exercised against the live API in CI (team-plan P4-1). Model from
``ONEGUARD_ANTHROPIC_MODEL`` (default ``claude-opus-5``), credentials resolved by the
SDK from the environment (``ANTHROPIC_API_KEY``; server-side only, never logged).
Effort is ``low``: these are short extraction calls under a hard deadline. Every
failure raises ProviderUnavailable.
"""

from __future__ import annotations

import os
from typing import Any

from oneguard.llm.provider import ProviderUnavailable
from oneguard.llm.validate import parse_and_validate, with_deadline

MODEL_ENV = "ONEGUARD_ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-opus-5"
KEY_ENV = "ANTHROPIC_API_KEY"
MAX_TOKENS = 4096


class AnthropicProvider:
    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self.model = model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
        if client is None:
            if not os.environ.get(KEY_ENV, "").strip():
                raise ProviderUnavailable(f"{KEY_ENV} is not set")
            try:
                import anthropic
            except ImportError:
                raise ProviderUnavailable("the anthropic package is not installed") from None
            client = anthropic.Anthropic(max_retries=0)
        self._client = client

    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        def call() -> Any:
            return self._client.with_options(timeout=timeout_s).messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
            )

        try:
            response = with_deadline(call, timeout_s)
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any SDK / network error is "unavailable"
            raise ProviderUnavailable(f"anthropic call failed: {type(exc).__name__}") from None
        stop = getattr(response, "stop_reason", None)
        if stop in ("refusal", "max_tokens"):
            raise ProviderUnavailable(f"anthropic stopped: {stop}")
        text = next(
            (b.text for b in getattr(response, "content", []) if getattr(b, "type", "") == "text"),
            None,
        )
        return parse_and_validate(text, schema)
