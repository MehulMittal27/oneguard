"""Concrete LLM providers by name (P4 owns this file; provider.py resolves through it).

Constructing a provider without its key or package raises ProviderUnavailable, so
``get_provider`` degrades to NullProvider (rules.md P8).
"""

from __future__ import annotations

from collections.abc import Callable

from oneguard.llm.anthropic_provider import AnthropicProvider
from oneguard.llm.openai_provider import OpenAIProvider
from oneguard.llm.provider import Provider

PROVIDERS: dict[str, Callable[[], Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}

__all__ = ["PROVIDERS", "AnthropicProvider", "OpenAIProvider"]
