"""The operator gate on ``/api/dev/*`` and C1's limits (docs/api-contract.md §1.2, §3.2, §3.8).

Production (``ONEGUARD_ENV=prod``) needs ``X-OneGuard-Operator`` equal to
``ONEGUARD_OPERATOR_TOKEN`` on every operator route; elsewhere the check is skipped. C1
refuses an instruction over 1,000 characters (422) and an 11th draft for one customer
within a minute (429).
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
import pytest

from oneguard.api import routes_dev
from oneguard.api.models import MAX_INSTRUCTION_CHARS
from oneguard.api.operator import OPERATOR_HEADER, TOKEN_ENV, operator_headers
from oneguard.api.ratelimit import DRAFTS_PER_MINUTE, SlidingWindowLimiter
from oneguard.viseca import demo
from tests.test_api_contract import (  # noqa: F401  fixtures
    FORM,
    confirm_form,
    db_url,
    running,
    seeded_db,
)

TOKEN = "op-secret-4f1c9a"
API = "http://oneguard.test"


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEGUARD_ENV", "prod")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def dev_routes() -> list[tuple[str, str]]:
    """Every (method, path) under ``/api/dev``, path parameters filled with a dummy id."""
    out = []
    for route in routes_dev.router.routes:
        path = re.sub(r"\{[^}]+\}", "X1", route.path)  # type: ignore[attr-defined]
        for method in sorted(route.methods):  # type: ignore[attr-defined]
            out.append((method, path))
    return out


def test_every_dev_route_is_behind_the_gate() -> None:
    assert len(dev_routes()) >= 10
    assert all(path.startswith("/api/dev/") for _, path in dev_routes())
    assert any(dep.dependency.__name__ == "require_operator" for dep in routes_dev.router.dependencies)


def test_off_production_the_gate_is_skipped(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ONEGUARD_ENV", "dev")
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    async def scenario() -> None:
        async with running(db_url) as run:
            assert (await run.get("/api/dev/soft-signals")).status_code == 200
            # a wrong header changes nothing off production either
            r = await run.http.get("/api/dev/soft-signals", headers={OPERATOR_HEADER: "wrong"})
            assert r.status_code == 200

    asyncio.run(scenario())


def test_in_production_every_dev_route_needs_the_token(
    db_url: str,  # noqa: F811
    prod: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        async with running(db_url) as run:
            for method, path in dev_routes():
                body = {} if method == "POST" else None
                missing = await run.http.request(method, path, json=body)
                assert missing.status_code == 401, (method, path, missing.text)
                assert missing.json() == {
                    "error": {"code": "operator_required", "message": f"Operator endpoints need the {OPERATOR_HEADER} header."}
                }
                wrong = await run.http.request(method, path, json=body, headers={OPERATOR_HEADER: TOKEN + "x"})
                assert wrong.status_code == 401, (method, path)
                assert wrong.json()["error"] == {"code": "operator_required", "message": "The operator token was not accepted."}
                assert TOKEN not in wrong.text

            right = await run.http.get("/api/dev/soft-signals", headers={OPERATOR_HEADER: TOKEN})
            assert right.status_code == 200 and set(right.json()) == {"live", "replay"}
            toggled = await run.http.post("/api/dev/soft-signals", json={"enabled": False}, headers={OPERATOR_HEADER: TOKEN})
            assert toggled.status_code == 200 and toggled.json() == {"enabled": False}

            # the customer API and D9 are not operator endpoints
            assert (await run.get("/api/customers")).status_code == 200
            assert (await run.get("/api/scenarios")).status_code == 200

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert TOKEN not in caplog.text


def test_a_refused_operator_call_changes_nothing(db_url: str, prod: None) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url) as run:
            before = (await run.http.get("/api/dev/soft-signals", headers={OPERATOR_HEADER: TOKEN})).json()
            r = await run.http.post("/api/dev/soft-signals", json={"enabled": not before["live"]})
            assert r.status_code == 401
            assert (await run.http.get("/api/dev/soft-signals", headers={OPERATOR_HEADER: TOKEN})).json() == before

    asyncio.run(scenario())


def test_production_without_a_token_refuses_rather_than_opens(
    db_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONEGUARD_ENV", "prod")
    monkeypatch.setenv(TOKEN_ENV, "  ")

    async def scenario() -> None:
        async with running(db_url) as run:
            for headers in ({}, {OPERATOR_HEADER: ""}, {OPERATOR_HEADER: "anything"}):
                r = await run.http.get("/api/dev/soft-signals", headers=headers)
                assert r.status_code == 503
                assert r.json()["error"]["code"] == "operator_unconfigured"

    asyncio.run(scenario())


def test_scripts_send_the_token_from_their_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert operator_headers() == {}
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    assert operator_headers() == {OPERATOR_HEADER: TOKEN}

    seen: list[tuple[str, str | None]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get(OPERATOR_HEADER)))
        return httpx.Response(200, json={})

    async def calls() -> None:
        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(reply)) as http:
            await demo._call(http, "GET", "/api/dev/runs/current")
            await demo._call(http, "GET", "/api/customers/CU0001/decisions")

    asyncio.run(calls())
    # the operator endpoint gets the token; the customer API never sees it
    assert seen == [("/api/dev/runs/current", TOKEN), ("/api/customers/CU0001/decisions", None)]


def test_demo_offline_through_the_gate(db_url: str, prod: None, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url) as run:
            transport = httpx.ASGITransport(app=run.app)
            lines: list[str] = []
            monkeypatch.delenv(TOKEN_ENV)
            code = await demo.offline("SCEN0000", api_base=API, card_id="CA0001", speed_ms=0, transport=transport, out=lines.append)
            # the server kept its token; this terminal has none: refused, with the fix named
            assert code == 1
            assert lines == [
                f"{API} refused: Operator endpoints are off: this server has no {TOKEN_ENV} set. (operator_unconfigured)"
            ]
            monkeypatch.setenv(TOKEN_ENV, TOKEN)
            await confirm_form(run, "CA0001")
            lines.clear()
            code = await demo.offline("SCEN0000", api_base=API, card_id="CA0001", speed_ms=0, transport=transport, out=lines.append)
            assert code == 0, lines

    asyncio.run(scenario())


def test_demo_names_the_variable_when_the_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "operator_required", "message": "The operator token was not accepted."}})

    async def call() -> demo.ApiRefused:
        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(reply)) as http:
            with pytest.raises(demo.ApiRefused) as refused:
                await demo._call(http, "POST", "/api/dev/replay/restart", {})
            return refused.value

    refused = asyncio.run(call())
    assert refused.status == 401 and refused.code == "operator_required"
    assert refused.message == f"The operator token was not accepted. Set {TOKEN_ENV} to the server's operator token."


# C1 limits ---------------------------------------------------------------------------------


def test_c1_takes_1000_characters_and_refuses_1001(db_url: str) -> None:  # noqa: F811
    base = "Groceries only, at most CHF 120 per order. "
    exact = (base * 30)[:MAX_INSTRUCTION_CHARS]
    assert len(exact) == 1000

    async def scenario() -> None:
        async with running(db_url) as run:
            ok = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": exact})
            assert ok.status_code == 200, ok.text
            assert ok.json()["instruction"] == exact
            too_long = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": exact + "."})
            assert too_long.status_code == 422
            assert too_long.json() == {
                "error": {
                    "code": "validation",
                    "message": "The instruction is longer than 1,000 characters.",
                    "detail": {"max_chars": 1000, "chars": 1001},
                }
            }
            # an unknown card is still a 404 first
            assert (await run.post("/api/cards/NOPE/policy-drafts", json={"instruction": exact + "."})).status_code == 404

    asyncio.run(scenario())


def test_c1_refuses_the_11th_draft_in_a_minute_for_one_customer(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url) as run:
            clock = [1000.0]
            run.services.draft_limiter = SlidingWindowLimiter(DRAFTS_PER_MINUTE, clock=lambda: clock[0])

            async def draft(card: str = "CA0001") -> httpx.Response:
                return await run.post(f"/api/cards/{card}/policy-drafts", json={"form": FORM})

            for i in range(DRAFTS_PER_MINUTE):
                clock[0] += 1.0
                assert (await draft()).status_code == 200, i
            eleventh = await draft()
            assert eleventh.status_code == 429
            assert eleventh.headers["retry-after"] == "51"
            assert eleventh.json() == {
                "error": {
                    "code": "rate_limited",
                    "message": "At most 10 drafts a minute. Try again in 51 seconds.",
                    "detail": {"limit": 10, "window_s": 60, "retry_after_s": 51},
                }
            }
            # an instruction draft counts the same way, and a refused one never reaches the compiler
            r = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": "groceries, max CHF 50"})
            assert r.status_code == 429
            # another customer's card is not held back (CA0039 is CU0019's)
            assert (await draft("CA0039")).status_code == 200
            # once the first draft is a minute old, one more fits
            clock[0] = 1001.0 + 60.0
            assert (await draft()).status_code == 200
            assert (await draft()).status_code == 429

    asyncio.run(scenario())


def test_sliding_window_limiter() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(2, window_s=60.0, clock=lambda: now[0])
    assert limiter.acquire("a") is None
    now[0] = 30.0
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == pytest.approx(30.0)
    assert limiter.acquire("b") is None
    now[0] = 60.0  # the first call leaves the window
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == pytest.approx(30.0)
