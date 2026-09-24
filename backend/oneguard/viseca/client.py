"""Async client for the Viseca sandbox (vendor/viseca-2026/technical_details.md).

One method per endpoint in "All API calls in one place". The bearer key comes from
``VISECA_API_KEY`` and the base URL from ``VISECA_BASE_URL``; both stay server-side.
The key is sent only in the ``Authorization`` header: it is never logged, never put
in an exception and never written to ``viseca_calls`` (CLAUDE.md rule 8).

Every call, successful or not, is summarised as a ``CallRecord`` and handed to the
client's sink; ``store_sink`` appends it to the ``viseca_calls`` table (docs/database.md
§2) with request and response bodies capped at 4 KB. Sinks run off the event loop so
logging never delays a decision.

A non-2xx response raises ``VisecaError`` with the HTTP status and the ``code`` from the
platform's JSON error envelope ``{"error": {"code", "message", "detail"?}}``. A network
failure or timeout raises it with ``status=None`` and code ``upstream_unavailable``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self

import httpx
from sqlalchemy import Engine

log = logging.getLogger(__name__)

BASE_URL_ENV = "VISECA_BASE_URL"
API_KEY_ENV = "VISECA_API_KEY"
DEFAULT_BASE_URL = "https://saw26api.ashyground-364e1d07.switzerlandnorth.azurecontainerapps.io"
DEFAULT_TIMEOUT_S = 10.0
LONG_POLL_GRACE_S = 10.0
"""Read timeout on top of ``wait`` for the long-poll, so the server answers first."""
SUMMARY_LIMIT = 4096
"""Largest request / response summary stored in ``viseca_calls`` (characters)."""
UPSTREAM_UNAVAILABLE = "upstream_unavailable"
REDACTED = "[redacted]"


class VisecaError(Exception):
    """A failed Viseca call: HTTP status (None for network errors) and platform code."""

    def __init__(
        self, status: int | None, code: str, message: str, detail: Any = None
    ) -> None:
        super().__init__(f"Viseca {status or 'network'} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


class VisecaNotConfigured(RuntimeError):
    """``VISECA_API_KEY`` is not set, so no authenticated call can be made."""


@dataclass(frozen=True)
class CallRecord:
    """One ``viseca_calls`` row: a summary of a request, never the key."""

    called_at: datetime
    method: str
    path: str
    status_code: int | None
    latency_ms: float
    request_summary: str | None
    response_summary: str | None
    error: str | None


CallSink = Callable[[CallRecord], None]


def store_sink(engine: Engine | None = None) -> CallSink:
    """A sink that appends each call to ``viseca_calls`` in its own transaction."""
    from oneguard.store.db import session
    from oneguard.store.schema import VisecaCall

    def write(record: CallRecord) -> None:
        with session(engine) as s:
            s.add(
                VisecaCall(
                    called_at=record.called_at,
                    method=record.method,
                    path=record.path,
                    status_code=record.status_code,
                    latency_ms=record.latency_ms,
                    request_summary=record.request_summary,
                    response_summary=record.response_summary,
                    error=record.error,
                )
            )

    return write


def cap(text: str | None, limit: int = SUMMARY_LIMIT) -> str | None:
    """``text`` cut to at most ``limit`` characters, marked when cut."""
    if text is None or len(text) <= limit:
        return text
    marker = f"...[{len(text) - limit} more chars]"
    return text[: limit - len(marker)] + marker


class VisecaClient:
    """One team's connection to the sandbox. Use as ``async with`` or call ``aclose``."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sink: CallSink | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        base = base_url or os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self._key = key.strip()
        self.base_url = base.rstrip("/")
        self._timeout_s = timeout_s
        self._sink = sink
        self._pending_logs: set[asyncio.Future[None]] = set()
        self._http = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=timeout_s
        )

    def __repr__(self) -> str:
        return f"VisecaClient(base_url={self.base_url!r}, key={'set' if self._key else 'unset'})"

    @property
    def configured(self) -> bool:
        """True when a bearer key is available."""
        return bool(self._key)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.drain()
        await self._http.aclose()

    async def drain(self) -> None:
        """Wait until every call summary has reached the sink."""
        while self._pending_logs:
            await asyncio.gather(*list(self._pending_logs), return_exceptions=True)

    # Transport ----------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, REDACTED) if self._key else text

    def _summary(self, value: Any) -> str | None:
        if value is None:
            return None
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
        return cap(self._scrub(text))

    def _emit(self, record: CallRecord) -> None:
        log.debug(
            "viseca %s %s -> %s in %.0f ms%s",
            record.method,
            record.path,
            record.status_code if record.status_code is not None else "no response",
            record.latency_ms,
            f" ({record.error})" if record.error else "",
        )
        if self._sink is None:
            return
        sink = self._sink

        def write() -> None:
            try:
                sink(record)
            except Exception:  # the call log must never break a call
                log.exception("could not record a Viseca call summary")

        future = asyncio.get_running_loop().run_in_executor(None, write)
        self._pending_logs.add(future)
        future.add_done_callback(self._pending_logs.discard)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        authenticated: bool = True,
        timeout_s: float | None = None,
        response: Literal["json", "text"] = "json",
    ) -> Any:
        headers = {"Accept": "application/json" if response == "json" else "text/csv, */*"}
        if authenticated:
            if not self._key:
                raise VisecaNotConfigured(f"{API_KEY_ENV} is not set; the Viseca API needs a bearer key")
            headers["Authorization"] = f"Bearer {self._key}"
        shown = path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        called_at = datetime.now(UTC)
        started = time.perf_counter()
        status: int | None = None
        summary: str | None = None
        error: str | None = None
        try:
            try:
                reply = await self._http.request(
                    method,
                    path,
                    params=params,
                    json=body,
                    headers=headers,
                    timeout=timeout_s or self._timeout_s,
                )
            except httpx.HTTPError as exc:
                error = self._scrub(f"{type(exc).__name__}: {exc}")[:500]
                raise VisecaError(None, UPSTREAM_UNAVAILABLE, error) from None
            status = reply.status_code
            if status >= 400:
                summary = self._summary(reply.text)
                err = self._error(reply)
                error = f"{err.code}: {err.message}"[:500]
                raise err
            if status == 204 or not reply.content:
                return None
            if response == "text":
                summary = f"<{reply.headers.get('content-type', 'text')} {len(reply.content)} bytes>"
                return reply.text
            summary = self._summary(reply.text)
            try:
                return reply.json()
            except ValueError:
                error = "response is not JSON"
                raise VisecaError(status, "invalid_response", "response is not JSON") from None
        finally:
            self._emit(
                CallRecord(
                    called_at=called_at,
                    method=method,
                    path=self._scrub(shown),
                    status_code=status,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    request_summary=self._summary(body),
                    response_summary=summary,
                    error=error,
                )
            )

    def _error(self, reply: httpx.Response) -> VisecaError:
        try:
            envelope = reply.json()
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
            err = envelope["error"]
            return VisecaError(
                reply.status_code,
                str(err.get("code") or f"http_{reply.status_code}"),
                self._scrub(str(err.get("message") or reply.reason_phrase)),
                err.get("detail"),
            )
        return VisecaError(
            reply.status_code, f"http_{reply.status_code}", self._scrub(reply.text[:200] or reply.reason_phrase)
        )

    # Endpoints (technical_details.md "All API calls in one place") ------------------

    async def healthz(self) -> dict[str, Any]:
        """``GET /healthz``: availability and version; no key needed."""
        return await self._request("GET", "/healthz", authenticated=False)

    async def bootstrap(self) -> dict[str, Any]:
        """``GET /v1/bootstrap``: versions, scenarios, timeouts, limits, features."""
        return await self._request("GET", "/v1/bootstrap")

    async def reference_data(self) -> dict[str, Any]:
        """``GET /v1/reference-data``: catalogues, fx rates, history-file metadata."""
        return await self._request("GET", "/v1/reference-data")

    async def authorization_history_csv(self) -> str:
        """``GET /v1/reference-data/authorization-history.csv``: the history file."""
        return await self._request(
            "GET", "/v1/reference-data/authorization-history.csv", response="text", timeout_s=60.0
        )

    async def create_mandate(
        self,
        instruction: str,
        hard_rules: list[dict[str, Any]],
        uncertainty_policy: str,
        guidance: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/mandates``: store a draft; the response carries ``draft_id``."""
        return await self._request(
            "POST",
            "/v1/mandates",
            body={
                "instruction": instruction,
                "hard_rules": hard_rules,
                "uncertainty_policy": uncertainty_policy,
                "guidance": guidance or [],
                "open_questions": open_questions or [],
            },
        )

    async def confirm_mandate(self, draft_id: str) -> dict[str, Any]:
        """``POST /v1/mandates/{draft_id}/confirm``: activate; returns ``mandate_id``."""
        return await self._request("POST", f"/v1/mandates/{draft_id}/confirm", body={"confirmed": True})

    async def get_mandate(self, mandate_id: str) -> dict[str, Any]:
        """``GET /v1/mandates/{mandate_id}``."""
        return await self._request("GET", f"/v1/mandates/{mandate_id}")

    async def patch_mandate(self, mandate_id: str, **fields: Any) -> dict[str, Any]:
        """``PATCH /v1/mandates/{mandate_id}``: tighten only; omitted fields unchanged."""
        return await self._request("PATCH", f"/v1/mandates/{mandate_id}", body=fields)

    async def delete_mandate(self, mandate_id: str) -> dict[str, Any] | None:
        """``DELETE /v1/mandates/{mandate_id}``: revoke."""
        return await self._request("DELETE", f"/v1/mandates/{mandate_id}")

    async def create_run(self, scenario_id: str, mandate_id: str) -> dict[str, Any]:
        """``POST /v1/scenario-runs``: start a run bound to an active mandate."""
        return await self._request(
            "POST", "/v1/scenario-runs", body={"scenario_id": scenario_id, "mandate_id": mandate_id}
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """``GET /v1/scenario-runs/{run_id}``: progress and event counters."""
        return await self._request("GET", f"/v1/scenario-runs/{run_id}")

    async def next_decision_request(self, wait: float = 25) -> dict[str, Any] | None:
        """``GET /v1/decision-requests/next?wait=``: the envelope, or None on 204."""
        wait_param = int(wait) if float(wait).is_integer() else wait
        return await self._request(
            "GET",
            "/v1/decision-requests/next",
            params={"wait": wait_param},
            timeout_s=float(wait) + LONG_POLL_GRACE_S,
        )

    async def post_decision(
        self,
        authorization_id: str,
        decision: str,
        *,
        reason_codes: list[str] | None = None,
        customer_message: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        engine_version: str | None = None,
    ) -> dict[str, Any] | None:
        """``POST /v1/authorizations/{id}/decision``: approve, decline or step_up."""
        body: dict[str, Any] = {"authorization_id": authorization_id, "decision": decision}
        optional = {
            "reason_codes": reason_codes,
            "customer_message": customer_message,
            "evidence": evidence,
            "engine_version": engine_version,
        }
        body.update({k: v for k, v in optional.items() if v is not None})
        return await self._request("POST", f"/v1/authorizations/{authorization_id}/decision", body=body)

    async def resolve(
        self,
        authorization_id: str,
        decision: str,
        customer_message: str,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """``POST /v1/authorizations/{id}/resolve``: the human answer after a step_up."""
        return await self._request(
            "POST",
            f"/v1/authorizations/{authorization_id}/resolve",
            body={"decision": decision, "customer_message": customer_message, "evidence": evidence or []},
        )

    async def list_authorizations(self, **params: Any) -> dict[str, Any]:
        """``GET /v1/authorizations``: pending and final runtime authorizations."""
        return await self._request("GET", "/v1/authorizations", params=params or None)

    async def events(self, since: int | str = 0) -> dict[str, Any]:
        """``GET /v1/events?since=``: the feed; continue from the returned ``next_cursor``."""
        return await self._request("GET", "/v1/events", params={"since": since})

    async def reset_team(self) -> dict[str, Any] | None:
        """``POST /v1/team/reset``: clear development state (disabled during judging)."""
        return await self._request("POST", "/v1/team/reset")
