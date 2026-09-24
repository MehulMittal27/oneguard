"""LLM providers behind the compiler (lane P4): fake SDK clients, no network.

Every failure — no key, error, timeout, refusal, truncation, off-schema output — must be
ProviderUnavailable so the compiler takes its fallback (rules.md P8).
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from oneguard.compiler.llm import SCHEMA as COMPILER_SCHEMA
from oneguard.llm.anthropic_provider import AnthropicProvider
from oneguard.llm.openai_provider import OpenAIProvider
from oneguard.llm.provider import NullProvider, ProviderUnavailable, get_provider

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "integer"}},
}


class FakeOpenAI:
    def __init__(self, content: str | None = None, *, exc: Exception | None = None, delay: float = 0.0,
                 finish: str = "stop", refusal: str | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content, self._exc, self._delay, self._finish, self._refusal = content, exc, delay, finish, refusal

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        time.sleep(self._delay)
        if self._exc:
            raise self._exc
        message = SimpleNamespace(content=self._content, refusal=self._refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self._finish)])


class FakeAnthropic:
    def __init__(self, text: str | None = None, *, stop: str = "end_turn", exc: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.timeout: float | None = None
        self.messages = SimpleNamespace(create=self._create)
        self._text, self._stop, self._exc = text, stop, exc

    def with_options(self, timeout: float) -> FakeAnthropic:
        self.timeout = timeout
        return self

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self._exc:
            raise self._exc
        return SimpleNamespace(stop_reason=self._stop, content=[SimpleNamespace(type="text", text=self._text)])


def test_openai_returns_validated_json_with_strict_schema_mode():
    client = FakeOpenAI(json.dumps({"answer": 42}))
    provider = OpenAIProvider(client=client)
    assert provider.complete_json(SCHEMA, "sys", "user", 2.0) == {"answer": 42}
    fmt = client.kwargs["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is SCHEMA
    assert client.kwargs["model"] == "gpt-4o-mini" and client.kwargs["timeout"] == 2.0


def test_openai_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("ONEGUARD_OPENAI_MODEL", "gpt-test")
    assert OpenAIProvider(client=FakeOpenAI("{}")).model == "gpt-test"


@pytest.mark.parametrize("client", [
    FakeOpenAI("not json"),
    FakeOpenAI(json.dumps({"answer": "forty-two"})),
    FakeOpenAI(json.dumps({"answer": 1, "extra": True})),
    FakeOpenAI(None),
    FakeOpenAI(json.dumps({"answer": 1}), finish="length"),
    FakeOpenAI(None, refusal="no"),
    FakeOpenAI(exc=RuntimeError("boom")),
], ids=["not-json", "wrong-type", "extra-key", "empty", "truncated", "refusal", "error"])
def test_openai_failures_are_unavailable(client):
    with pytest.raises(ProviderUnavailable):
        OpenAIProvider(client=client).complete_json(SCHEMA, "sys", "user", 2.0)


def test_openai_honours_the_timeout():
    provider = OpenAIProvider(client=FakeOpenAI(json.dumps({"answer": 1}), delay=1.0))
    started = time.monotonic()
    with pytest.raises(ProviderUnavailable, match="within"):
        provider.complete_json(SCHEMA, "sys", "user", 0.1)
    assert time.monotonic() - started < 0.8


def test_anthropic_returns_validated_json_via_output_config():
    client = FakeAnthropic(json.dumps({"answer": 7}))
    assert AnthropicProvider(client=client).complete_json(SCHEMA, "sys", "user", 3.0) == {"answer": 7}
    assert client.kwargs["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert client.kwargs["model"] == "claude-opus-5" and client.timeout == 3.0


@pytest.mark.parametrize("client", [
    FakeAnthropic(json.dumps({"answer": 7}), stop="refusal"),
    FakeAnthropic(json.dumps({"answer": 7}), stop="max_tokens"),
    FakeAnthropic("{"),
    FakeAnthropic(exc=RuntimeError("boom")),
], ids=["refusal", "max-tokens", "bad-json", "error"])
def test_anthropic_failures_are_unavailable(client):
    with pytest.raises(ProviderUnavailable):
        AnthropicProvider(client=client).complete_json(SCHEMA, "sys", "user", 2.0)


@pytest.mark.parametrize("name,key", [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
def test_a_provider_without_its_key_degrades_to_null(monkeypatch, name, key):
    monkeypatch.delenv(key, raising=False)
    assert isinstance(get_provider(name), NullProvider)


def test_the_compiler_schema_meets_strict_mode():
    """OpenAI strict mode: every object closes additionalProperties and requires every key."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(COMPILER_SCHEMA)
