"""OpenAI provider (P4): structured outputs in JSON-schema mode.

Model from ``ONEGUARD_OPENAI_MODEL`` (default ``gpt-4.1``, docs/decisions.md), key from
``OPENAI_API_KEY`` (server-side only; never logged). Every failure — missing package,
missing key, timeout, refusal, truncation, API error, output off-schema — raises
ProviderUnavailable, so callers take their deterministic path.
"""

from __future__ import annotations

import os
from typing import Any

from oneguard.llm.provider import ProviderUnavailable
from oneguard.llm.validate import parse_and_validate, with_deadline

MODEL_ENV = "ONEGUARD_OPENAI_MODEL"
DEFAULT_MODEL = "gpt-4.1"
KEY_ENV = "OPENAI_API_KEY"


class OpenAIProvider:
    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self.model = model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
        if client is None:
            if not os.environ.get(KEY_ENV, "").strip():
                raise ProviderUnavailable(f"{KEY_ENV} is not set")
            try:
                import openai
            except ImportError:
                raise ProviderUnavailable("the openai package is not installed") from None
            client = openai.OpenAI(max_retries=0)
        self._client = client

    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        def call() -> Any:
            return self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "oneguard_output", "schema": schema, "strict": True},
                },
                temperature=0,
                timeout=timeout_s,
            )

        try:
            response = with_deadline(call, timeout_s)
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any SDK / network error is "unavailable"
            raise ProviderUnavailable(f"openai call failed: {type(exc).__name__}") from None
        try:
            choice = response.choices[0]
        except (AttributeError, IndexError):
            raise ProviderUnavailable("openai returned no choices") from None
        if getattr(choice, "finish_reason", None) == "length":
            raise ProviderUnavailable("openai output was truncated")
        message = choice.message
        if getattr(message, "refusal", None):
            raise ProviderUnavailable("openai refused the request")
        return parse_and_validate(message.content, schema)
