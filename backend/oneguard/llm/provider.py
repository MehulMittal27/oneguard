"""The one interface to generative models (frozen interface file, docs/team-contract.md §3).

Used by the policy compiler, tier-2 fact extraction and tier-3 explanation rewrite
(docs/rules.md §4a). Concrete providers live in ``llm/registry.py`` (P4); this file
never changes when they do. With no provider every caller falls back to its
deterministic path (rules.md P8).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

PROVIDER_ENV = "ONEGUARD_LLM_PROVIDER"
PROVIDER_NAMES: tuple[str, ...] = ("openai", "anthropic", "null")


class ProviderUnavailable(RuntimeError):
    """The model cannot answer: not configured, no key, timed out, or failed."""


@runtime_checkable
class Provider(Protocol):
    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        """Return a JSON object that validates against ``schema`` within ``timeout_s``.

        Raises ProviderUnavailable on any failure; never returns unvalidated output.
        """
        ...


class NullProvider:
    """No model configured. Every call raises ProviderUnavailable."""

    def complete_json(
        self, schema: dict[str, Any], system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        raise ProviderUnavailable("no LLM provider configured")


def provider_available(provider: Provider | None) -> bool:
    """True when a real provider is configured (it may still fail per call)."""
    return provider is not None and not isinstance(provider, NullProvider)


def get_provider(name: str | None = None) -> Provider:
    """The provider named by ``name`` or ``ONEGUARD_LLM_PROVIDER`` (default ``null``).

    A provider that cannot start (missing key, missing package) degrades to
    NullProvider with a warning. An unknown name is a configuration error.
    """
    from oneguard.llm.registry import PROVIDERS

    chosen = (name if name is not None else os.environ.get(PROVIDER_ENV, "")).strip().lower()
    chosen = chosen or "null"
    if chosen == "null":
        return NullProvider()
    if chosen not in PROVIDERS:
        raise ValueError(f"{PROVIDER_ENV}={chosen!r}; expected one of {', '.join(PROVIDER_NAMES)}")
    try:
        return PROVIDERS[chosen]()
    except ProviderUnavailable as exc:
        log.warning("LLM provider %s unavailable, using none: %s", chosen, exc)
        return NullProvider()
