"""Concrete LLM providers by name (P4 owns this file; provider.py resolves through it).

The classes below are placeholders until P4 lands them: constructing one raises
ProviderUnavailable, so ``get_provider`` degrades to NullProvider.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oneguard.llm.provider import Provider, ProviderUnavailable


class OpenAIProvider:
    def __init__(self) -> None:
        raise ProviderUnavailable("openai provider not implemented yet")

    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        raise ProviderUnavailable("openai provider not implemented yet")


class AnthropicProvider:
    def __init__(self) -> None:
        raise ProviderUnavailable("anthropic provider not implemented yet")

    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        raise ProviderUnavailable("anthropic provider not implemented yet")


PROVIDERS: dict[str, Callable[[], Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}
