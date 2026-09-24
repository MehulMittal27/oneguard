"""docs/api-contract.md Appendix A, line by line, against the app and the fake sandbox.

Every checklist line has a key in ``APPENDIX_A``; each test says which lines it covers
with ``@covers(...)``. ``test_every_appendix_line_is_covered`` fails when the contract
gains a line no test covers, or a key stops matching the contract's wording.

The app runs its real lifespan on a throwaway SQLite copy of the seeded store. Live runs
go through the real ``VisecaWorker`` against ``tests/fake_viseca.py`` (in process, over
``httpx.ASGITransport``). The engine is ``TEST_ENGINE``: stubs except rules, decide and
explain, which give all three outcomes by amount (SCEN0001: 4 approve, 4 step-up,
2 decline) and a message that names the total, so the API's side of each line is tested
without depending on which engine lanes have landed.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from oneguard.api import queries
from oneguard.api.app import AppConfig, create_app, sanitise
from oneguard.api.policies import (
    FORM_INSTRUCTION,
    NO_CAP_QUESTION,
    NO_CHECKS_QUESTION,
    per_order_cap,
)
from oneguard.api.services import Services
from oneguard.engine import stubs
from oneguard.engine.interfaces import load_implementations
from oneguard.engine.tier3 import rewrite_explanation
from oneguard.engine.types import (
    CompiledDraft,
    EngineDecision,
    EvidenceRow,
    Explanation,
    Facts,
    Rule,
    RuleResult,
)
from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.schema import Decision, Mandate, PolicyDraft, Run
from oneguard.viseca.client import VisecaClient, store_sink
from oneguard.viseca.demo import rule_to_viseca
from tests.fake_viseca import FakeConfig, FakeViseca

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "docs" / "api-contract.md"

# Appendix A ------------------------------------------------------------------------------

APPENDIX_A = {
    "nine_endpoints": "All nine endpoints on `/api`, served from the same origin as the app",
    "static_last": "Static file mount registered **after** every API route",
    "envelopes": "Envelope per endpoint exactly as §1.0 (wrapped vs. bare vs. `204`)",
    "keys_present": "Every documented key present; nullable keys present with explicit `null`",
    "arrays": "`cards`, `items`, `evidence`, `reason_codes`, `open_questions` are arrays, never `null`",
    "matrix": "`decision` / `uncertain_outcome` / `status` only ever in a combination from §3.1a's matrix",
    "deadline_stable": "`deadline_at` present on every `pending_human` row, stable across polls",
    "timestamps": "Every timestamp ISO-8601 UTC with `Z`, fixed width, no mixed offsets",
    "clocks": "`occurred_at` is simulated time; `deadline_at` is the real clock",
    "evidence_number": "`evidence` non-empty on every decision, `message` names the actual number",
    "check_wording": "Limit checks worded exactly as §3.9, or the meter shows nothing",
    "amount_delivery": "`amount` includes delivery; `billing_amount_chf` present on every row",
    "order_returnable": "`order_returnable` is one of the four strings; `unknown` / `not_applicable` / `null` distinct",
    "c6_full_history": "C6 returns the full history for that customer only, on every poll",
    "id_stable": "`authorization_id` unique and stable",
    "no_secrets": "Viseca key server-side only; nothing secret reachable from `/api` responses",
    "injection_flag": "`injection_flag` set by the backend; `null` when clean, never omitted",
    "closed_window": "A resolve for a closed window is refused, not recorded",
    "lapse_survives_reload": "Lapsed step-ups closed server-side so expiry survives a reload",
    "revoke_policy_only": "Revoke touches the policy only, never a purchase in flight",
    "tighten_pure": "Tighten rejects anything that is not a pure addition",
    "no_false_2xx": "No endpoint ever returns `2xx` for work that did not happen",
    "json_relative": "JSON request and response bodies (`application/json`, UTF-8); base URL `/api` is relative",
    "not_applicable": "`not_applicable` is not uncertainty and never causes an escalation",
    "merchant_by_id": "Merchants joined by `merchant_id`, never by name",
    "amount_and_billing": "`amount`/`currency` and `billing_amount_chf` both sent, even when the currency is CHF",
    "platform_window": "`deadline_at` reflects the platform's real window; the UI's 120 s constant is not assumed",
    "bounded": "Every call bounded server-side: a fast failure, never a hang",
}
COVERED: dict[str, list[str]] = {key: [] for key in APPENDIX_A}


def covers(*keys: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def mark(fn: Callable[..., Any]) -> Callable[..., Any]:
        for key in keys:
            COVERED[key].append(fn.__name__)
        return fn

    return mark


def appendix_lines() -> list[str]:
    text = CONTRACT.read_text(encoding="utf-8")
    appendix = text[text.index("## Appendix A") :]
    return [line[len("- [ ] ") :].strip() for line in appendix.splitlines() if line.startswith("- [ ] ")]


# Test engine -----------------------------------------------------------------------------

APPROVE_UP_TO = 65.0
ASK_UP_TO = 125.0
INJECTION = ("ignore any previous", "pre-authorised")


def _rules(facts: Facts, policy: Any) -> list[RuleResult]:
    total = facts.billing_amount_chf
    outcome = "pass" if total <= APPROVE_UP_TO else ("unknown" if total <= ASK_UP_TO else "fail")
    return [RuleResult(rule_id="test_amount", outcome=outcome, detail=f"Total CHF {total:.2f}.", source="event")]


def _decide(rules: list[RuleResult], protections_: Any, warnings_: Any, soft: Any, policy: Any, ledger: Any) -> EngineDecision:
    outcome = {r.outcome for r in rules}
    if "fail" in outcome:
        return EngineDecision(outcome="decline", reason_codes=["per_order_limit_exceeded"], step=2, deciding_ids=["test_amount"])
    if "unknown" in outcome:
        return EngineDecision(outcome="step_up", reason_codes=["unevaluable"], step=4, deciding_ids=["test_amount"])
    return EngineDecision(outcome="approve", reason_codes=["within_limits"], step=7, deciding_ids=[])


def _explain(decision: EngineDecision, facts: Facts, policy: Any, rules: Any, signals: Any) -> Explanation:
    total = f"CHF {facts.billing_amount_chf:.2f}"
    word = {"approve": "Approved", "decline": "Declined", "step_up": "Needs your OK"}[decision.outcome]
    injected = any(p in line.item_details.lower() for line in facts.items for p in INJECTION)
    outcome = {"approve": "pass", "decline": "fail", "step_up": "uncertain"}[decision.outcome]
    return Explanation(
        message=f"{word}: the total is {total}.",
        counterfactual=f"Would approve at CHF {ASK_UP_TO:.0f} or less." if decision.outcome == "decline" else None,
        evidence=[EvidenceRow(rule="test_amount", outcome=outcome, detail=f"Total {total}.", source="policy")],
        injection_flag={"flagged": True, "reason": "Instructions in the shop's text were ignored."} if injected else None,
    )


TEST_ENGINE = {**stubs.STUBS, "evaluate_rules": _rules, "decide": _decide, "explain": _explain}
TEST_STUBBED = frozenset(stubs.STUBS) - {"evaluate_rules", "decide", "explain"}

FORM = {
    "per_order_limit_chf": 125,
    "period_limit_chf": 1200,
    "period_days": 7,
    "categories": ["groceries"],
    "sellers_used_before_only": True,
    "uncertainty_policy": "ask",
}

# Harness ---------------------------------------------------------------------------------


class Clock:
    """The real clock plus an offset the test moves forward."""

    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset


class Faulty(httpx.AsyncBaseTransport):
    """Wraps the fake: answers ``(method, path prefix)`` with an error, or hangs."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.fail: dict[tuple[str, str], int] = {}
        self.hang = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.hang:
            await asyncio.sleep(30)
        for (method, prefix), status in self.fail.items():
            if request.method == method and request.url.path.startswith(prefix):
                return httpx.Response(status, json={"error": {"code": "unavailable", "message": "injected"}})
        return await self.inner.handle_async_request(request)


@dataclass
class Running:
    app: Any
    http: httpx.AsyncClient
    fake: FakeViseca | None
    faulty: Faulty | None
    clock: Clock
    responses: list[httpx.Response] = field(default_factory=list)

    @property
    def services(self) -> Services:
        return self.app.state.services

    async def get(self, path: str, **kw: Any) -> httpx.Response:
        r = await self.http.get(path, **kw)
        self.responses.append(r)
        return r

    async def post(self, path: str, **kw: Any) -> httpx.Response:
        r = await self.http.post(path, **kw)
        self.responses.append(r)
        return r

    async def decisions(self, customer_id: str = "CU0001") -> list[dict[str, Any]]:
        r = await self.get(f"/api/customers/{customer_id}/decisions")
        assert r.status_code == 200, r.text
        return r.json()["decisions"]


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture
def db_url(seeded_db: Path, tmp_path: Path) -> str:
    path = tmp_path / "oneguard.sqlite"
    shutil.copy(seeded_db, path)
    return f"sqlite:///{path}"


def fast(**overrides: Any) -> FakeConfig:
    return FakeConfig(**{"decision_deadline_s": 3.0, "human_window_s": 60.0, "max_wait_s": 0.2, **overrides})


@asynccontextmanager
async def running(
    db_url: str,
    *,
    fake: FakeViseca | None = None,
    clock: Clock | None = None,
    client_timeout_s: float = 10.0,
    ready: str = "polling",
    **config: Any,
) -> AsyncIterator[Running]:
    clock = clock or Clock()
    faulty = Faulty(httpx.ASGITransport(app=fake.app)) if fake else None

    def client(db: Any) -> VisecaClient | None:
        if fake is None:
            return None
        return VisecaClient(
            "http://fake-viseca", fake.config.api_key, transport=faulty, sink=store_sink(db), timeout_s=client_timeout_s
        )

    options = {
        "database_url": db_url,
        "viseca_client": client,
        "worker_options": {"poll_wait_s": 0.2},
        "implementations": TEST_ENGINE,
        "stubbed": TEST_STUBBED,
        "signals_backend": "off",
        "frontend_dist": Path("/nonexistent-dist"),
        "now": clock,
        **config,
    }
    app = create_app(AppConfig(**options))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://oneguard.test") as http:
            run = Running(app, http, fake, faulty, clock)
            if fake is not None:
                await until(lambda: run.services.worker.status().state == ready)
            yield run


async def until(condition: Callable[[], Any], timeout: float = 20.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if isinstance(value, Awaitable):
            value = await value
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


async def confirm_form(run: Running, card_id: str = "CA0001", **form: Any) -> dict[str, Any]:
    r = await run.post(f"/api/cards/{card_id}/policy-drafts", json={"form": {**FORM, **form}})
    assert r.status_code == 200, r.text
    draft = r.json()
    r = await run.post(
        f"/api/policy-drafts/{draft['draft_id']}/confirm",
        json={"checks": draft["checks"], "uncertainty_policy": draft["uncertainty_policy"], "open_questions": []},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def live_run(run: Running, scenario_id: str = "SCEN0001", card_id: str = "CA0001", n: int = 10) -> list[dict[str, Any]]:
    """A confirmed policy, a Viseca run started through D3, all ``n`` decisions in C6, every
    step-up's deadline the platform's."""
    await confirm_form(run, card_id)
    r = await run.post("/api/dev/runs", json={"scenario_id": scenario_id, "card_id": card_id})
    assert r.status_code == 200, r.text
    customer = {"CA0001": "CU0001", "CA0039": "CU0019"}[card_id]

    async def settled() -> list[dict[str, Any]] | None:
        # A step-up is listed before the reply to its POST moves deadline_at to the
        # platform's expiry; on a loaded machine that gap can pass a second.
        listed = await run.decisions(customer)
        if len(listed) != n:
            return None
        expiry = {a.live_id: a.expires_at for a in run.fake.all_auths()}
        for d in listed:
            expires = expiry.get(d["authorization_id"])
            if d["status"] == "pending_human" and (
                expires is None
                or not d["deadline_at"]
                or abs((datetime.fromisoformat(d["deadline_at"]) - expires).total_seconds()) > 1.0
            ):
                return None
        return listed

    return await until(settled)


async def replay(run: Running, scenario_id: str, card_id: str, n: int, customer: str) -> list[dict[str, Any]]:
    r = await run.post("/api/dev/replay/restart", json={"scenario_id": scenario_id, "card_id": card_id, "speed_ms": 0})
    assert r.status_code == 200, r.text

    async def done() -> bool:
        status = (await run.get("/api/dev/replay")).json()
        return not status["running"] and status["delivered"] == n

    await until(done)
    return await run.decisions(customer)


def by_total(decisions: list[dict[str, Any]], total: float) -> dict[str, Any]:
    (found,) = [d for d in decisions if d["billing_amount_chf"] == total]
    return found


TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TIMESTAMP_KEYS = {"occurred_at", "deadline_at", "confirmed_at", "period_window_start", "as_of", "next_at", "run_started_at"}
DECISION_KEYS = {
    "authorization_id", "customer_id", "card_id", "decision", "uncertain_outcome", "status", "reason_codes",
    "message", "uncertainty", "occurred_at", "merchant", "amount", "currency", "billing_amount_chf", "items",
    "injection_flag", "evidence", "order_returnable", "delivery_by",
    "counterfactual", "related", "session", "merchant_meta", "engine_version", "latency_ms", "explanation_source",
    "run_id", "run_started_at",
}  # fmt: skip
NULLABLE_DECISION_KEYS = {"uncertain_outcome", "uncertainty", "injection_flag", "delivery_by", "counterfactual", "related", "session"}
MATRIX = {
    ("approved", None, "final"),
    ("stopped", None, "final"),
    ("uncertain", "pending", "pending_human"),
    ("uncertain", "approved", "final"),
    ("uncertain", "declined", "final"),
    ("uncertain", "expired", "final"),
}


def walk(obj: Any) -> list[tuple[str, Any]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append((k, v))
            out.extend(walk(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(walk(v))
    return out


# The coverage check ----------------------------------------------------------------------


def test_every_appendix_line_is_covered() -> None:
    assert sorted(APPENDIX_A.values()) == sorted(appendix_lines())
    assert {k: v for k, v in COVERED.items() if not v} == {}


# Shape -----------------------------------------------------------------------------------

NINE = [
    ("GET", "/api/customers"),
    ("GET", "/api/customers/{customer_id}/accounts"),
    ("GET", "/api/customers/{customer_id}/decisions"),
    ("POST", "/api/cards/{card_id}/policy-drafts"),
    ("POST", "/api/policy-drafts/{draft_id}/confirm"),
    ("GET", "/api/cards/{card_id}/policy"),
    ("POST", "/api/cards/{card_id}/policy/tighten"),
    ("POST", "/api/cards/{card_id}/policy/revoke"),
    ("POST", "/api/authorizations/{authorization_id}/resolve"),
]


@covers("nine_endpoints", "static_last", "json_relative")
def test_nine_endpoints_on_api_and_the_ui_mounted_last(db_url: str, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")

    async def scenario() -> None:
        async with running(db_url, frontend_dist=dist) as run:
            paths = run.app.openapi()["paths"]
            assert all(method.lower() in paths.get(path, {}) for method, path in NINE)
            assert all(path.startswith("/api/") for path in paths)
            assert type(run.app.routes[-1]).__name__ == "Mount" and run.app.routes[-1].path == ""
            page = await run.get("/")
            assert page.status_code == 200 and "<title>app</title>" in page.text
            api = await run.get("/api/customers")
            assert api.headers["content-type"].startswith("application/json")
            missing = await run.get("/api/no-such-thing")
            assert missing.status_code == 404
            assert missing.json() == {"error": {"code": "not_found", "message": "No endpoint /api/no-such-thing."}}
            not_json = await run.post("/api/authorizations/x/resolve", content=b"approve")
            assert not_json.status_code == 422 and not_json.json()["error"]["code"] == "validation"

    asyncio.run(scenario())


@covers("static_last")
def test_without_a_build_the_root_is_a_placeholder_page(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url) as run:
            page = await run.get("/")
            assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
            assert "OneGuard is running" in page.text
            assert (await run.get("/api/customers")).status_code == 200

    asyncio.run(scenario())


@covers("envelopes", "keys_present", "arrays", "json_relative", "amount_delivery", "amount_and_billing")
def test_envelopes_keys_and_arrays(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            decisions = await live_run(run)
            # wrapped
            customers = (await run.get("/api/customers")).json()
            assert set(customers) == {"customers"}
            alex = next(c for c in customers["customers"] if c["customer_id"] == "CU0001")
            assert alex == {
                "customer_id": "CU0001", "name": "Alex Meier", "home_region": "Zurich region",
                "card_id": "CA0001", "scenario_ids": ["SCEN0000", "SCEN0001"], "live": True,
            }  # fmt: skip
            other = next(c for c in customers["customers"] if c["customer_id"] == "CU0002")
            assert other["card_id"] is None and other["scenario_ids"] == [] and other["live"] is False
            accounts = (await run.get("/api/customers/CU0001/accounts")).json()
            assert set(accounts) == {"accounts"}
            for account in accounts["accounts"]:
                assert isinstance(account["cards"], list) and account["cards"]
                assert set(account) == {
                    "account_id", "customer_id", "account_type", "account_purpose", "status",
                    "per_transaction_limit_chf", "monthly_limit_chf", "cards",
                }  # fmt: skip
            assert set((await run.get("/api/customers/CU0001/decisions")).json()) == {"decisions"}
            policy = (await run.get("/api/cards/CA0001/policy")).json()
            assert set(policy) == {"mandate"}
            assert (await run.get("/api/cards/CA0002/policy")).json() == {"mandate": None}
            # bare
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            assert {"draft_id", "card_id", "instruction", "checks", "uncertainty_policy", "open_questions", "dry_run"} <= set(draft)
            assert draft["compiler"] == "form" and isinstance(draft["open_questions"], list)
            assert policy["mandate"]["status"] == "active" and isinstance(policy["mandate"]["open_questions"], list)
            # no body
            step_up = next(d for d in decisions if d["status"] == "pending_human")
            resolved = await run.post(f"/api/authorizations/{step_up['authorization_id']}/resolve", json={"decision": "approve"})
            assert resolved.status_code == 204 and resolved.content == b""
            revoked = await run.post("/api/cards/CA0001/policy/revoke")
            assert revoked.status_code == 204 and revoked.content == b""

            events = {a.live_id: a.event for a in run.fake.all_auths()}
            for d in await run.decisions():
                assert DECISION_KEYS <= set(d), DECISION_KEYS - set(d)
                for key in NULLABLE_DECISION_KEYS:
                    assert key in d
                for key in ("items", "evidence", "reason_codes"):
                    assert isinstance(d[key], list)
                assert d["items"] and d["evidence"] and d["reason_codes"]
                auth = events[d["authorization_id"]]["authorization"]
                # amount is the platform's total with delivery, billing CHF on every row
                assert d["amount"] == auth["amount"] == round(auth["items_subtotal"] + auth["delivery_fee"], 2)
                assert (d["currency"], d["billing_amount_chf"]) == ("CHF", auth["billing_amount_chf"])
                assert d["merchant"] == {"merchant_id": auth["merchant"]["merchant_id"], "name": auth["merchant"]["merchant_name"]}
            for r in run.responses:
                if r.content:
                    assert r.headers["content-type"].startswith("application/json")
                    r.content.decode("utf-8")
                    assert "http://" not in r.text and "https://" not in r.text

    asyncio.run(scenario())


# Correctness -----------------------------------------------------------------------------


@covers("matrix", "deadline_stable", "clocks", "evidence_number", "id_stable", "platform_window", "timestamps")
def test_decisions_across_their_lifecycle(db_url: str) -> None:
    async def scenario() -> None:
        clock = Clock()
        async with running(db_url, fake=FakeViseca(fast(human_window_s=60.0)), clock=clock) as run:
            first = await live_run(run)
            second = await run.decisions()
            assert [d["authorization_id"] for d in first] == [d["authorization_id"] for d in second]
            assert len({d["authorization_id"] for d in first}) == 10
            outcomes = sorted(d["decision"] for d in first)
            assert outcomes == ["approved"] * 4 + ["stopped"] * 2 + ["uncertain"] * 4

            fake_auths = {a.live_id: a for a in run.fake.all_auths()}
            pending = [d for d in first if d["status"] == "pending_human"]
            for d in pending:
                again = next(x for x in second if x["authorization_id"] == d["authorization_id"])
                assert d["deadline_at"] and again["deadline_at"] == d["deadline_at"]
                deadline = datetime.fromisoformat(d["deadline_at"])
                accepted = fake_auths[d["authorization_id"]].accepted_at
                # the platform's 60 s window from its accepted time, not the UI's 120 s
                assert abs((deadline - accepted).total_seconds() - 60.0) <= 1.0
            for d in first:
                auth = fake_auths[d["authorization_id"]].event["authorization"]
                assert d["occurred_at"] == auth["timestamp"]  # simulated
                assert d["occurred_at"].startswith("2026-08")
                assert d["evidence"]
                assert f"CHF {d['billing_amount_chf']:.2f}" in d["message"]

            # answer one, let the others lapse
            a, b, c, e = pending
            assert (await run.post(f"/api/authorizations/{a['authorization_id']}/resolve", json={"decision": "approve"})).status_code == 204
            assert (await run.post(f"/api/authorizations/{b['authorization_id']}/resolve", json={"decision": "decline"})).status_code == 204
            clock.offset = timedelta(seconds=90)
            final = {d["authorization_id"]: d for d in await run.decisions()}
            assert final[a["authorization_id"]]["uncertain_outcome"] == "approved"
            assert final[a["authorization_id"]]["resolved_by"] == "customer"
            assert final[b["authorization_id"]]["uncertain_outcome"] == "declined"
            for lapsed in (c, e):
                row = final[lapsed["authorization_id"]]
                assert (row["uncertain_outcome"], row["status"], row["resolved_by"]) == ("expired", "final", "timeout")
                # rules.md Q2, with the platform's 60 s window
                assert row["message"] == "Expired: no answer within 60 s; nothing was approved."
                assert row["counterfactual"] is None and row["explanation_source"] == lapsed["explanation_source"]
            for d in final.values():
                assert (d["decision"], d["uncertain_outcome"], d["status"]) in MATRIX
                assert ("deadline_at" in d) == (d["status"] == "pending_human")
            for r in run.responses:
                if r.content and r.headers["content-type"].startswith("application/json"):
                    for key, value in walk(r.json()):
                        if key in TIMESTAMP_KEYS and value is not None:
                            assert TIMESTAMP.match(value), (key, value)

    asyncio.run(scenario())


@covers("check_wording")
def test_limit_checks_are_worded_for_the_meter(db_url: str) -> None:
    per_order = re.compile(r"total at or below chf\s*([\d,]+(?:\.\d+)?)\s*per order", re.IGNORECASE)
    period = re.compile(r"total at or below chf\s*([\d,]+(?:\.\d+)?)\s*across any (\d+) days", re.IGNORECASE)

    async def scenario() -> None:
        async with running(db_url) as run:
            form = {**FORM, "per_order_limit_chf": 1250.5, "period_limit_chf": 300, "period_days": 14}
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": form})).json()
            texts = [c["text"] for c in draft["checks"]]
            assert "Total at or below CHF 1250.50 per order" in texts
            assert "Total at or below CHF 300 across any 14 days" in texts
            assert [float(m[1]) for t in texts if (m := per_order.search(t))] == [1250.5]
            assert [(float(m[1]), int(m[2])) for t in texts if (m := period.search(t))] == [(300.0, 14)]
            mandate = await confirm_form(run, **form)
            assert mandate["usage"]["per_order_limit_chf"] == 1250.5
            assert (mandate["usage"]["period_limit_chf"], mandate["usage"]["period_days"]) == (300.0, 14)

    asyncio.run(scenario())


@covers("order_returnable", "not_applicable", "injection_flag", "merchant_by_id", "c6_full_history")
def test_offline_replay_terms_injection_merchants_and_one_customer(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url) as run:
            shoes = await replay(run, "SCEN0002", "CA0011", 12, "CU0006")
            monitor = await replay(run, "SCEN0004", "CA0039", 11, "CU0019")
            groceries = await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            for customer, rows, card in (("CU0006", shoes, "CA0011"), ("CU0019", monitor, "CA0039"), ("CU0001", groceries, "CA0001")):
                assert {(d["customer_id"], d["card_id"]) for d in rows} == {(customer, card)}
                again = await run.decisions(customer)
                assert [d["authorization_id"] for d in again] == [d["authorization_id"] for d in rows]
            with session(run.services.db_engine) as s:
                stored = s.scalars(select(Decision.customer_id)).all()
            assert sorted(stored) == sorted(["CU0006"] * 12 + ["CU0019"] * 11 + ["CU0001"] * 10)

            terms = {d["order_returnable"] for d in shoes + monitor + groceries}
            assert terms == {"true", "false", "unknown", "not_applicable"}
            unknown = by_total(shoes, 175.0)
            assert unknown["order_returnable"] == "unknown" and unknown["delivery_by"] is None
            digital = by_total(monitor, 195.0)
            assert digital["order_returnable"] == "not_applicable"
            assert digital["decision"] == "stopped" and digital["uncertainty"] is None
            assert all(row["outcome"] != "uncertain" for row in digital["evidence"])

            flagged = [d for d in monitor if d["injection_flag"] is not None]
            assert flagged and all(d["injection_flag"]["flagged"] is True for d in flagged)
            assert all("injection_flag" in d for d in shoes + monitor + groceries)
            assert all(d["injection_flag"] is None for d in shoes + groceries)

            # PixelHarbour (a lookalike name) is its own merchant id, unknown to the customer
            known = run.services.history.known_merchants("CU0019")
            for d in monitor:
                meta = d["merchant_meta"]
                assert meta["familiar"] == (d["merchant"]["merchant_id"] in known)
            lookalike = [d for d in monitor if d["merchant"]["name"] == "PixelHarbour"]
            original = [d for d in monitor if d["merchant"]["name"] == "PixelHarbor"]
            assert lookalike and original
            assert {d["merchant"]["merchant_id"] for d in lookalike} != {d["merchant"]["merchant_id"] for d in original}
            assert all(not d["merchant_meta"]["familiar"] for d in lookalike)

    asyncio.run(scenario())


def test_each_decision_names_its_run_and_when_the_run_started(db_url: str) -> None:
    """Two runs on one card: C6 carries each decision's run id and its run's start (§2)."""

    async def scenario() -> None:
        clock = Clock()
        async with running(db_url, clock=clock) as run:
            first = await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            clock.offset = timedelta(minutes=5)
            both = await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            assert len({d["authorization_id"] for d in both}) == 20
            first_ids = {d["authorization_id"] for d in first}
            runs: dict[str, set[str]] = {}
            for d in both:
                assert d["run_id"] and TIMESTAMP.match(d["run_started_at"])
                runs.setdefault(d["run_id"], set()).add(d["run_started_at"])
            assert len(runs) == 2 and all(len(starts) == 1 for starts in runs.values())
            (older,) = {d["run_id"] for d in both if d["authorization_id"] in first_ids}
            (newer,) = set(runs) - {older}
            (older_start,), (newer_start,) = runs[older], runs[newer]
            gap = datetime.fromisoformat(newer_start) - datetime.fromisoformat(older_start)
            assert timedelta(minutes=5) <= gap < timedelta(minutes=6)
            with session(run.services.db_engine) as s:
                stored = dict(s.execute(select(Run.run_id, Run.started_at)).all())
            assert {older, newer} <= set(stored)

    asyncio.run(scenario())


# Safety ----------------------------------------------------------------------------------


@covers("no_secrets")
def test_nothing_secret_reaches_a_response(db_url: str) -> None:
    async def scenario() -> None:
        fake = FakeViseca(fast())
        async with running(db_url, fake=fake) as run:
            await live_run(run)
            await run.get("/healthz")
            await run.get("/api/dev/ledger/CA0001")
            await run.get("/api/dev/replay")
            run.faulty.fail[("POST", "/v1/mandates")] = 503
            await run.post("/api/policy-drafts/unknown/confirm", json={"checks": [], "uncertainty_policy": "ask", "open_questions": []})
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            failed = await run.post(
                f"/api/policy-drafts/{draft['draft_id']}/confirm",
                json={"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []},
            )
            assert failed.status_code == 503
            assert len(run.responses) > 10
            for r in run.responses:
                assert fake.config.api_key not in r.text
                assert "Bearer" not in r.text and "sqlite:" not in r.text and ".sqlite" not in r.text
            health = (await run.get("/healthz")).json()
            assert health["database"]["engine"] == "sqlite"

    asyncio.run(scenario())


def test_healthz_error_lines_carry_no_url_or_credential() -> None:
    line = "poll failed: ConnectError https://api.example/v1/x?key=abc postgresql://u:p@h/db password=hunter2"
    assert sanitise(line) == "poll failed: ConnectError [url] [url] password=[redacted]"


@covers("closed_window", "no_false_2xx")
def test_a_resolve_after_the_window_is_refused_and_not_recorded(db_url: str) -> None:
    async def scenario() -> None:
        clock = Clock()
        async with running(db_url, fake=FakeViseca(fast(human_window_s=60.0)), clock=clock) as run:
            decisions = await live_run(run)
            step_up = next(d for d in decisions if d["status"] == "pending_human")
            live_id = step_up["authorization_id"]
            clock.offset = timedelta(seconds=61)  # the window is over; no expiry has run yet
            r = await run.post(f"/api/authorizations/{live_id}/resolve", json={"decision": "approve"})
            assert r.status_code == 409 and r.json()["error"]["code"] == "window_closed"
            auth = run.fake.auths[live_id]
            assert [x["decision"] for x in auth.resolutions] == ["decline"]  # the timeout, not the customer
            row = next(d for d in await run.decisions() if d["authorization_id"] == live_id)
            assert (row["uncertain_outcome"], row["resolved_by"]) == ("expired", "timeout")
            again = await run.post(f"/api/authorizations/{live_id}/resolve", json={"decision": "approve"})
            assert again.status_code == 409 and again.json()["error"]["code"] == "not_awaiting_answer"

    asyncio.run(scenario())


def test_resolve_rules(db_url: str) -> None:
    """C8: 404 unknown, 409 not a step-up, 204 once, 409 the second time, 422 bad body."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            decisions = await live_run(run)
            approved = next(d for d in decisions if d["decision"] == "approved")
            step_up = next(d for d in decisions if d["status"] == "pending_human")
            r = await run.post("/api/authorizations/lv_nope/resolve", json={"decision": "approve"})
            assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
            r = await run.post(f"/api/authorizations/{approved['authorization_id']}/resolve", json={"decision": "approve"})
            assert r.status_code == 409 and r.json()["error"]["code"] == "not_awaiting_answer"
            r = await run.post(f"/api/authorizations/{step_up['authorization_id']}/resolve", json={"decision": "maybe"})
            assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
            ok = await run.post(f"/api/authorizations/{step_up['authorization_id']}/resolve", json={"decision": "approve"})
            assert ok.status_code == 204
            assert [x["decision"] for x in run.fake.auths[step_up["authorization_id"]].resolutions] == ["approve"]
            twice = await run.post(f"/api/authorizations/{step_up['authorization_id']}/resolve", json={"decision": "decline"})
            assert twice.status_code == 409
            row = next(d for d in await run.decisions() if d["authorization_id"] == step_up["authorization_id"])
            assert (row["decision"], row["uncertain_outcome"], row["status"], row["resolved_by"]) == (
                "uncertain", "approved", "final", "customer",
            )  # fmt: skip

    asyncio.run(scenario())


@covers("lapse_survives_reload")
def test_a_lapsed_step_up_reads_as_expired_after_a_restart(db_url: str) -> None:
    async def scenario() -> None:
        clock = Clock()
        async with running(db_url, clock=clock) as run:
            rows = await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            pending = {d["authorization_id"] for d in rows if d["status"] == "pending_human"}
            assert len(pending) == 4
        clock.offset = timedelta(seconds=121)  # the app was down while the windows lapsed
        async with running(db_url, clock=clock) as run:
            rows = {d["authorization_id"]: d for d in await run.decisions()}
            for live_id in pending:
                assert (rows[live_id]["uncertain_outcome"], rows[live_id]["resolved_by"]) == ("expired", "timeout")
                # the offline expiry re-renders the message too (rules.md Q2, default 120 s window)
                assert rows[live_id]["message"] == "Expired: no answer within 120 s; nothing was approved."
                assert rows[live_id]["counterfactual"] is None
        async with running(db_url, clock=clock) as run:
            rows = {d["authorization_id"]: d for d in await run.decisions()}
            assert all(rows[i]["uncertain_outcome"] == "expired" for i in pending)
            with session(run.services.db_engine) as s:
                stored = {d.live_authorization_id: d for d in s.scalars(select(Decision))}
            assert all(stored[i].final and stored[i].resolved_by == "timeout" and stored[i].spent_chf == 0 for i in pending)

    asyncio.run(scenario())


@covers("revoke_policy_only")
def test_revoke_flips_the_policy_and_leaves_purchases_alone(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            before = await live_run(run)
            mandate = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (await run.post("/api/cards/CA0001/policy/revoke")).status_code == 204
            after = await run.decisions()
            assert after == before  # nothing in flight changed
            revoked = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (revoked["mandate_id"], revoked["status"]) == (mandate["mandate_id"], "revoked")
            assert revoked["checks"] == mandate["checks"]  # kept, never deleted
            (tm,) = run.fake.mandates
            assert run.fake.mandates[tm]["status"] == "revoked"
            assert all(a.status == "pending" for a in run.fake.all_auths() if not a.decisions or a.decisions[0]["decision"] == "step_up")
            with session(run.services.db_engine) as s:
                row = s.scalar(select(Mandate))
                assert row.status == "revoked" and row.revoked_at is not None
            # idempotent, and a card without any policy is 404
            assert (await run.post("/api/cards/CA0001/policy/revoke")).status_code == 204
            assert (await run.post("/api/cards/CA0002/policy/revoke")).status_code == 404
            # a revoked card can take a new policy
            fresh = await confirm_form(run)
            assert fresh["status"] == "active" and fresh["mandate_id"] != mandate["mandate_id"]

    asyncio.run(scenario())


@covers("tighten_pure")
def test_tighten_accepts_only_pure_additions(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            base = {**FORM, "period_limit_chf": None, "period_days": None, "categories": [], "sellers_used_before_only": False}
            mandate = await confirm_form(run, **base)
            assert [c["id"] for c in mandate["checks"]] == ["per_order_limit"]
            proposal = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": {**FORM, "per_order_limit_chf": 99}})).json()
            checks = {c["id"]: c for c in proposal["checks"]}

            async def tighten(body: dict[str, Any]) -> httpx.Response:
                return await run.post("/api/cards/CA0001/policy/tighten", json=body)

            changed = await tighten({"add_checks": [checks["per_order_limit"]]})
            assert changed.status_code == 409 and changed.json()["error"]["code"] == "not_pure_addition"
            unknown = await tighten({"add_checks": [{**checks["period_limit"], "id": "nope"}]})
            assert unknown.status_code == 422 and unknown.json()["error"]["detail"] == {"unknown": ["nope"]}
            loosen = await tighten({"add_checks": [], "uncertainty_policy": "ask"})
            assert loosen.status_code == 422
            empty = await tighten({"add_checks": []})
            assert empty.status_code == 409 and empty.json()["error"]["code"] == "not_pure_addition"
            same = await tighten({"add_checks": [mandate["checks"][0]]})
            assert same.status_code == 409

            ok = await tighten({"add_checks": [{**checks["period_limit"], "text": "edited"}], "uncertainty_policy": "decline"})
            assert ok.status_code == 200, ok.text
            tightened = ok.json()
            assert [c["id"] for c in tightened["checks"]] == ["per_order_limit", "period_limit"]
            assert tightened["checks"][1]["text"] == "Total at or below CHF 1200 across any 7 days"
            assert tightened["uncertainty_policy"] == "decline" and tightened["mandate_id"] == mandate["mandate_id"]
            (tm,) = run.fake.mandates
            stored = run.fake.mandates[tm]
            assert stored["uncertainty_policy"] == "decline" and len(stored["hard_rules"]) == 2
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["checks"] == tightened["checks"]

    asyncio.run(scenario())


@covers("no_false_2xx")
def test_a_platform_failure_changes_nothing(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            decisions = await live_run(run)
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            body = {"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []}
            run.faulty.fail[("POST", "/v1/mandates")] = 503
            r = await run.post(f"/api/policy-drafts/{draft['draft_id']}/confirm", json=body)
            assert r.status_code == 503 and r.json()["error"]["code"] == "upstream_unavailable"
            with session(run.services.db_engine) as s:
                assert s.get(PolicyDraft, draft["draft_id"]).confirmed_at is None

            step_up = next(d for d in decisions if d["status"] == "pending_human")
            run.faulty.fail[("POST", "/v1/authorizations")] = 503
            r = await run.post(f"/api/authorizations/{step_up['authorization_id']}/resolve", json={"decision": "approve"})
            assert r.status_code == 503 and r.json()["error"]["code"] == "upstream_unavailable"
            row = next(d for d in await run.decisions() if d["authorization_id"] == step_up["authorization_id"])
            assert row["status"] == "pending_human"

            run.faulty.fail[("PATCH", "/v1/mandates")] = 503
            proposal = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": {**FORM, "categories": ["household"]}})).json()
            policy = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            r = await run.post("/api/cards/CA0001/policy/tighten", json={"add_checks": [], "uncertainty_policy": "decline"})
            assert r.status_code == 503
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["uncertainty_policy"] == policy["uncertainty_policy"]
            assert proposal["draft_id"]

            run.faulty.fail.clear()
            ok = await run.post(f"/api/policy-drafts/{draft['draft_id']}/confirm", json=body)
            assert ok.status_code == 200

    asyncio.run(scenario())


# Also carried over -----------------------------------------------------------------------


@covers("bounded")
def test_a_hanging_platform_answers_fast(db_url: str) -> None:
    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast()), viseca_timeout_s=0.5) as run:
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            run.faulty.hang = True
            started = time.monotonic()
            r = await run.post(
                f"/api/policy-drafts/{draft['draft_id']}/confirm",
                json={"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []},
            )
            assert r.status_code == 503 and time.monotonic() - started < 3
            run.faulty.hang = False

    asyncio.run(scenario())


@covers("bounded")
def test_a_slow_compiler_or_database_answers_fast(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_compile(*_: Any) -> CompiledDraft:
        time.sleep(2)
        raise AssertionError("not reached in time")

    def slow_query(*_: Any) -> Any:
        time.sleep(2)

    async def scenario() -> None:
        engine = {**TEST_ENGINE, "compile_instruction": slow_compile}
        async with running(db_url, implementations=engine, compile_timeout_s=0.2, db_timeout_s=0.2) as run:
            started = time.monotonic()
            r = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": "Groceries up to CHF 50."})
            assert r.status_code == 504 and r.json()["error"]["code"] == "compiler_timeout"
            monkeypatch.setattr(queries, "customers", slow_query)
            r = await run.get("/api/customers")
            assert r.status_code == 503 and r.json()["error"]["code"] == "upstream_unavailable"
            assert time.monotonic() - started < 2

    asyncio.run(scenario())


# Endpoint semantics beyond the checklist ------------------------------------------------


def test_policy_drafts_and_the_viseca_dance(db_url: str) -> None:
    """C1 (instruction and form) and C2: accepted ids, re-lint, draft + confirm at Viseca."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            assert (await run.post("/api/cards/CA9999/policy-drafts", json={"form": FORM})).status_code == 404
            for bad in ({}, {"instruction": "x", "form": FORM}, {"instruction": "   "}):
                r = await run.post("/api/cards/CA0001/policy-drafts", json=bad)
                assert r.status_code == 422 and r.json()["error"]["code"] == "validation"

            instruction = "Buy  groceries for me,\nnothing  fancy."
            r = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": instruction})
            assert r.status_code == 200
            compiled = r.json()
            assert compiled["instruction"] == instruction and compiled["compiler"] == "fallback"
            assert compiled["checks"] == [] and compiled["open_questions"][0] == NO_CHECKS_QUESTION
            r = await run.post(
                f"/api/policy-drafts/{compiled['draft_id']}/confirm",
                json={"checks": [], "uncertainty_policy": "ask", "open_questions": []},
            )
            assert r.status_code == 409 and r.json()["error"] == {
                "code": "lint_failed",
                "message": "Not confirmed: no restriction could be read.",
                "detail": {"missing": ["per_order_limit"]},
            }

            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            assert draft["dry_run"]["sample_size"] > 0 and len(draft["dry_run"]["examples"]) <= 3
            url = f"/api/policy-drafts/{draft['draft_id']}/confirm"
            r = await run.post(url, json={"checks": [{**draft["checks"][0], "id": "nope"}], "uncertainty_policy": "ask", "open_questions": []})
            assert r.status_code == 422 and r.json()["error"]["detail"] == {"unknown": ["nope"]}
            r = await run.post(url, json={"checks": draft["checks"][:2], "uncertainty_policy": "ask", "open_questions": []})
            assert r.status_code == 409 and r.json()["error"]["detail"] == {"missing": ["item_categories", "known_shop"]}
            edited = [{**c, "text": "anything at all"} for c in draft["checks"]]
            r = await run.post(url, json={"checks": edited, "uncertainty_policy": "decline", "open_questions": ["x"]})
            assert r.status_code == 200
            mandate = r.json()
            assert mandate["checks"] == draft["checks"]  # edited text ignored
            assert mandate["uncertainty_policy"] == "decline" and mandate["status"] == "active"
            assert mandate["usage"]["period_spent_chf"] == 0 and mandate["usage"]["pending_chf"] == 0
            again = await run.post(url, json={"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []})
            assert again.status_code == 409 and again.json()["error"]["code"] == "draft_confirmed"

            (tm,) = run.fake.mandates
            at_viseca = run.fake.mandates[tm]
            with session(run.services.db_engine) as s:
                row = s.scalar(select(Mandate))
                assert row.viseca_mandate_id == tm and row.instruction == draft["instruction"] == FORM_INSTRUCTION
            assert at_viseca["instruction"].endswith("Decline when uncertain.")
            assert at_viseca["uncertainty_policy"] == "decline"
            assert at_viseca["guidance"] == [c["text"] for c in draft["checks"]]
            assert [r["field"] for r in at_viseca["hard_rules"]] == [
                "authorization.billing_amount_chf", "authorization.billing_amount_chf",
                "items[].item_category", "merchant.known_shop",
            ]  # fmt: skip
            bound = run.services.worker._policies[tm]
            assert bound.mandate_id == mandate["mandate_id"] and bound.requires_known_shop
            assert [rule_to_viseca(r) for r in bound.rules] == at_viseca["hard_rules"]

            # a second policy replaces the first, which is revoked here and at Viseca
            second = await confirm_form(run, per_order_limit_chf=80)
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["mandate_id"] == second["mandate_id"]
            assert run.fake.mandates[tm]["status"] == "revoked"

    asyncio.run(scenario())


def test_the_customers_words_are_served_verbatim(db_url: str) -> None:
    """C1 -> C2 -> C3 -> C4 keep the instruction exactly as typed (unicode, spacing, line
    breaks); Viseca gets the same words. Checks and guidance never replace them."""
    words = "  Groceries only, max CHF 120 per order.\nMüsli & café  okay - ask me first!  "

    async def scenario() -> None:
        engine = {**TEST_ENGINE, "compile_instruction": load_implementations()["compile_instruction"]}
        async with running(db_url, fake=FakeViseca(fast()), implementations=engine) as run:
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": words})).json()
            assert draft["instruction"] == words and draft["checks"], draft
            r = await run.post(
                f"/api/policy-drafts/{draft['draft_id']}/confirm",
                json={"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []},
            )
            assert r.status_code == 200, r.text
            assert r.json()["instruction"] == words
            (tm,) = run.fake.mandates
            assert run.fake.mandates[tm]["instruction"] == words
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["instruction"] == words

            proposal = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            period = next(c for c in proposal["checks"] if c["id"] == "period_limit")
            r = await run.post("/api/cards/CA0001/policy/tighten", json={"add_checks": [period]})
            assert r.status_code == 200, r.text
            assert r.json()["instruction"] == words
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["instruction"] == words
            with session(run.services.db_engine) as s:
                assert s.scalar(select(Mandate)).instruction == words

    asyncio.run(scenario())


def test_a_form_policy_serves_built_from_the_form(db_url: str) -> None:
    """The form has no words of the customer's: C1, C2 and C3 serve "Built from the form",
    never the joined check texts. Viseca still gets the accepted checks as text."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": FORM})).json()
            assert draft["instruction"] == FORM_INSTRUCTION == "Built from the form"
            mandate = await confirm_form(run)
            assert mandate["instruction"] == FORM_INSTRUCTION
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["instruction"] == FORM_INSTRUCTION
            (tm,) = run.fake.mandates
            texts = [c["text"] for c in mandate["checks"]]
            assert run.fake.mandates[tm]["instruction"] == " ".join([*(f"{t}." for t in texts), "Ask me when uncertain."])

    asyncio.run(scenario())


def test_form_policies_stored_with_joined_checks_serve_built_from_the_form(db_url: str) -> None:
    """Rows written before the form path kept "Built from the form" held the joined check
    texts; a start sets them (and only them) to it. A typed instruction is left alone."""
    at = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    joined = "Total at or below CHF 20 per order. Ask me when uncertain."

    def rows(card_id: str, compiler: str, instruction: str, confirmed_at: datetime) -> list[Any]:
        common = {"card_id": card_id, "instruction": instruction, "rules": {}, "checks": [],
                  "uncertainty_policy": "ask", "open_questions": []}  # fmt: skip
        return [
            PolicyDraft(draft_id=f"pd_{card_id}", customer_id="CU0001", dry_run={}, compiler=compiler,
                        viseca_draft_id=None, created_at=confirmed_at, confirmed_at=confirmed_at, **common),
            Mandate(mandate_id=f"md_{card_id}", viseca_mandate_id=None, customer_id="CU0001", status="active",
                    confirmed_at=confirmed_at, revoked_at=None, **common),
        ]  # fmt: skip

    engine = make_engine(db_url)
    with session(engine) as s:
        s.add_all([*rows("CA0001", "form", joined, at), *rows("CA0002", "fallback", joined, at)])
    engine.dispose()

    async def scenario() -> None:
        async with running(db_url) as run:
            policy = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert policy["instruction"] == FORM_INSTRUCTION
            typed = (await run.get("/api/cards/CA0002/policy")).json()["mandate"]
            assert typed["instruction"] == joined
            with session(run.services.db_engine) as s:
                drafts = {d.card_id: d.instruction for d in s.scalars(select(PolicyDraft))}
            assert drafts == {"CA0001": FORM_INSTRUCTION, "CA0002": joined}
            assert queries.restore_form_instructions(run.services.db_engine, FORM_INSTRUCTION) == 0

    asyncio.run(scenario())


def test_a_draft_with_no_checks_asks_and_is_never_confirmed(db_url: str) -> None:
    """C1 with nothing readable asks for a limit or item type; C2 refuses it (409 lint_failed),
    sends nothing to Viseca and stores no mandate. A normal draft still confirms."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            r = await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": "buy something nice"})
            assert r.status_code == 200
            draft = r.json()
            assert draft["checks"] == []
            assert draft["open_questions"][0] == NO_CHECKS_QUESTION
            assert NO_CHECKS_QUESTION == "I couldn't read a spending limit or item type - try 'groceries, max CHF 120 per order'"
            assert NO_CAP_QUESTION not in draft["open_questions"]
            r = await run.post(
                f"/api/policy-drafts/{draft['draft_id']}/confirm",
                json={"checks": [], "uncertainty_policy": "ask", "open_questions": []},
            )
            assert r.status_code == 409 and r.json()["error"] == {
                "code": "lint_failed",
                "message": "Not confirmed: no restriction could be read.",
                "detail": {"missing": ["per_order_limit"]},
            }
            assert run.fake.mandates == {}
            with session(run.services.db_engine) as s:
                assert s.scalar(select(Mandate)) is None
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"] is None

            confirmed = await confirm_form(run)
            assert confirmed["status"] == "active" and len(run.fake.mandates) == 1

    asyncio.run(scenario())


def test_c2_relints_through_the_lint_accepted_interface(db_url: str) -> None:
    """C2 asks the registered ``lint_accepted``: a floor alone is no per-order cap (409),
    an exact price is (the gym renewal's "same price as last time")."""
    lint_accepted = load_implementations()["lint_accepted"]
    calls: list[list[str]] = []

    def recording_lint(rules: list[Rule], accepted_ids: list[str]) -> tuple[list[str], list[str]]:
        calls.append(accepted_ids)
        return lint_accepted(rules, accepted_ids)

    def amount_compiler(text: str, *_: Any) -> CompiledDraft:
        operator = text.split()[1]
        rule = Rule(id="C1", field="authorization.billing_amount_chf", operator=operator, value=59, currency="CHF",
                    scope="purchase", text=f"Total {operator} CHF 59", source="inferred", kind="amount")
        return CompiledDraft(instruction=text, rules=[rule], uncertainty_policy="ask", open_questions=[],
                             dry_run=stubs.STUBS["dry_run"](None, None, ""), compiler="llm")

    async def scenario() -> None:
        engine = {**TEST_ENGINE, "compile_instruction": amount_compiler, "lint_accepted": recording_lint}
        async with running(db_url, implementations=engine) as run:
            for operator, capped in ((">=", False), (">", False), ("=", True)):
                draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"instruction": f"Total {operator} 59"})).json()
                assert (NO_CAP_QUESTION not in draft["open_questions"]) is capped, operator
                r = await run.post(
                    f"/api/policy-drafts/{draft['draft_id']}/confirm",
                    json={"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []},
                )
                if capped:
                    assert r.status_code == 200, r.text
                else:
                    assert r.status_code == 409 and r.json()["error"] == {
                        "code": "lint_failed",
                        "message": "Not confirmed: the policy needs a limit on what one purchase may cost.",
                        "detail": {"missing": ["per_order_limit"]},
                    }, operator
            assert calls == [["C1"], ["C1"], ["C1"]]

    asyncio.run(scenario())


def test_an_exact_price_is_a_per_order_cap_and_a_floor_is_not() -> None:
    def amount(operator: str) -> Rule:
        return Rule(id="C1", field="authorization.billing_amount_chf", operator=operator, value=59, currency="CHF",
                    scope="purchase", text="Total", source="inferred", kind="amount")

    for operator in ("<", "<=", "="):
        assert per_order_cap([amount(operator)]) == (59.0, operator)
    for operator in (">=", ">"):
        assert per_order_cap([amount(operator)]) is None


def test_usage_follows_the_ledger(db_url: str) -> None:
    """C3 ``usage``: final approvals spent, waiting step-ups pending, a customer OK moves it."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            decisions = await live_run(run)
            usage = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["usage"]
            as_of = max(d["occurred_at"] for d in decisions)
            start = (datetime.fromisoformat(as_of) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            window = [d for d in decisions if start <= d["occurred_at"] <= as_of]
            spent = round(sum(d["billing_amount_chf"] for d in window if d["decision"] == "approved"), 2)
            pending = [d for d in window if d["status"] == "pending_human"]
            assert (usage["as_of"], usage["period_window_start"]) == (as_of, start)
            assert usage["period_spent_chf"] == spent
            assert usage["pending_chf"] == round(sum(d["billing_amount_chf"] for d in pending), 2)
            assert (usage["per_order_limit_chf"], usage["period_limit_chf"], usage["period_days"]) == (125.0, 1200.0, 7)

            ok = pending[0]
            assert (await run.post(f"/api/authorizations/{ok['authorization_id']}/resolve", json={"decision": "approve"})).status_code == 204
            after = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["usage"]
            assert after["period_spent_chf"] == round(spent + ok["billing_amount_chf"], 2)
            assert after["pending_chf"] == round(usage["pending_chf"] - ok["billing_amount_chf"], 2)

            ledger = (await run.get("/api/dev/ledger/CA0001")).json()
            assert ledger["period_spent_chf"] == after["period_spent_chf"] and ledger["frozen"] is False
            assert len(ledger["entries"]) == 10
            counted = {e["authorization_id"]: e["counted_chf"] for e in ledger["entries"]}
            assert counted[ok["authorization_id"]] == ok["billing_amount_chf"]

    asyncio.run(scenario())


def test_operator_endpoints(db_url: str) -> None:
    """D1–D6."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            assert (await run.get("/api/dev/replay")).status_code == 404
            r = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0001", "card_id": "CA0001"})
            assert r.status_code == 409  # no policy yet
            r = await run.post("/api/dev/replay/restart", json={"scenario_id": "SCEN0001", "card_id": "CA0011"})
            assert r.status_code == 422
            r = await run.post("/api/dev/replay/restart", json={"scenario_id": "SCEN9999", "card_id": "CA0001"})
            assert r.status_code == 404

            await confirm_form(run)
            r = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0000", "card_id": "CA0001"})
            assert r.status_code == 200
            live = r.json()
            assert live["scenario_id"] == "SCEN0000" and live["card_id"] == "CA0001"

            async def done() -> bool:
                status = (await run.get(f"/api/dev/runs/{live['run_id']}")).json()
                return status["decided"] == 1

            await until(done)
            assert (await run.get("/api/dev/runs/run_nope")).status_code == 404

            state = (await run.get("/api/dev/soft-signals")).json()
            assert state == {"live": run.services.live_models(), "replay": False}
            assert (await run.post("/api/dev/soft-signals", json={"enabled": True})).json() == {"enabled": True}
            assert run.services.worker._signals_enabled is True
            assert (await run.get("/api/dev/soft-signals")).json() == {"live": True, "replay": True}
            assert (await run.post("/api/dev/soft-signals", json={"enabled": False})).json() == {"enabled": False}
            assert run.services.worker._signals_enabled is False and run.services.live_models() is False
            assert (await run.get("/api/dev/soft-signals")).json() == {"live": False, "replay": False}

            rows = await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            assert len(rows) == 11  # the live one and the replay's ten
            assert all("signals=off" in d["engine_version"] for d in rows)
            status = (await run.get("/api/dev/replay")).json()
            assert status == {"scenario_id": "SCEN0001", "card_id": "CA0001", "delivered": 10, "total": 10, "running": False, "next_at": None}
            ledger = (await run.get("/api/dev/ledger/CA0001")).json()
            assert len(ledger["entries"]) == 10 and ledger["mandate_id"].startswith("md_")
            assert (await run.get("/api/dev/ledger/CA9999")).status_code == 404

            health = (await run.get("/healthz")).json()
            assert health["status"] == "ok" and health["worker"]["polling"] is True
            assert health["events_cursor"] == health["worker"]["events_cursor"] > 0
            assert health["database"]["ok"] and health["database"]["round_trip_ms"] is not None
            assert TIMESTAMP.match(health["worker"]["last_poll_at"])

    asyncio.run(scenario())


class Rephrases:
    """A tier-3 provider: the template with a friendlier opening, every number kept."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, schema: dict, system: str, user: str, timeout_s: float) -> dict:
        self.calls += 1
        template = json.loads(user)["template_message"]
        return {"message": f"Quick note: {template[0].lower()}{template[1:]}"}


def test_d5_switches_tier3_rewrites_on_and_off(db_url: str) -> None:
    """With the models on, C6 shows the template first and the rewrite on a later poll
    (``explanation_source: model``); D5 off stops rewrites of later decisions. The worker
    schedules a decision's rewrite as it notifies, so the test awaits those tasks."""

    async def scenario() -> None:
        provider = Rephrases()
        engine = {**TEST_ENGINE, "rewrite_explanation": rewrite_explanation}
        async with running(
            db_url, fake=FakeViseca(fast()), provider=provider, implementations=engine,
            stubbed=TEST_STUBBED - {"rewrite_explanation"},
        ) as run:
            worker = run.services.worker
            seen: list[Any] = []
            worker.add_listener(seen.append)
            assert run.services.live_models() is False  # signals off: models off until D5
            assert (await run.post("/api/dev/soft-signals", json={"enabled": True})).json() == {"enabled": True}
            await confirm_form(run)
            r = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0000", "card_id": "CA0001"})
            assert r.status_code == 200, r.text
            await until(lambda: len(seen) >= 1)  # the posted decision; its rewrite may follow at once
            await asyncio.wait_for(asyncio.gather(*worker._rewrites), 30)
            (row,) = await run.decisions()
            assert row["explanation_source"] == "model" and row["message"].startswith("Quick note: ")
            assert f"CHF {row['billing_amount_chf']:.2f}" in row["message"]
            assert provider.calls == 1

            assert (await run.post("/api/dev/soft-signals", json={"enabled": False})).json() == {"enabled": False}
            r = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0001", "card_id": "CA0001"})
            assert r.status_code == 200, r.text
            await until(lambda: len({d.authorization_id for d in seen}) == 11)
            assert not worker._rewrites and provider.calls == 1
            rows = await run.decisions()
            assert len(rows) == 11 and sum(d["explanation_source"] == "model" for d in rows) == 1

    asyncio.run(scenario())


class TakenLease:
    """The worker lease while another process holds it."""

    def acquire(self) -> bool:
        return False

    def held(self) -> bool:
        return False

    def release(self) -> None:
        return None


def test_healthz_shows_standby_while_another_process_holds_the_worker_lease(db_url: str) -> None:
    """A second process on the same store does not poll; /healthz says so and stays 200."""

    async def scenario() -> None:
        fake = FakeViseca(fast())
        options = {"poll_wait_s": 0.2, "lease": TakenLease(), "standby_retry_s": 0.05}
        async with running(db_url, fake=fake, ready="standby", worker_options=options) as run:
            await asyncio.sleep(0.3)
            r = await run.get("/healthz")
            assert r.status_code == 200
            worker = r.json()["worker"]
            assert (worker["state"], worker["polling"], worker["ok"], worker["last_poll_at"]) == (
                "standby", False, False, None,
            )  # fmt: skip
            assert r.json()["status"] == "degraded" and fake.polls == 0

    asyncio.run(scenario())


def test_d3_starts_nothing_while_runs_are_switched_off(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """ONEGUARD_ALLOW_RUNS=false: D3 is 409 runs_disabled and no run reaches the platform."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            await confirm_form(run)
            monkeypatch.setenv("ONEGUARD_ALLOW_RUNS", "false")
            assert (await run.get("/healthz")).json()["runs_allowed"] is False
            r = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0001", "card_id": "CA0001"})
            assert r.status_code == 409
            assert r.json()["error"] == {
                "code": "runs_disabled",
                "message": "Starting scenario runs is switched off (ONEGUARD_ALLOW_RUNS=false); nothing was started.",
            }
            assert run.fake.runs == {} and run.services.worker.status().runs == []
            monkeypatch.setenv("ONEGUARD_ALLOW_RUNS", "true")
            assert (await run.get("/healthz")).json()["runs_allowed"] is True
            ok = await run.post("/api/dev/runs", json={"scenario_id": "SCEN0000", "card_id": "CA0001"})
            assert ok.status_code == 200 and len(run.fake.runs) == 1

    asyncio.run(scenario())


def test_d7_shows_the_newest_run_live_or_replay(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """D7: 404 before any run; then whichever run started last, as D1's or D4's shape."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            none = await run.get("/api/dev/runs/current")
            assert none.status_code == 404
            assert none.json() == {"error": {"code": "not_found", "message": "No run has started yet."}}

            await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            current = (await run.get("/api/dev/runs/current")).json()
            assert current == (await run.get("/api/dev/replay")).json()

            await confirm_form(run)
            live = (await run.post("/api/dev/runs", json={"scenario_id": "SCEN0000", "card_id": "CA0001"})).json()
            current = (await run.get("/api/dev/runs/current")).json()
            assert current["run_id"] == live["run_id"]

            async def decided() -> bool:
                return (await run.get("/api/dev/runs/current")).json()["decided"] == 1

            await until(decided)  # counters settled: D7 and D4 read the same run alike
            current = (await run.get("/api/dev/runs/current")).json()
            assert current == (await run.get(f"/api/dev/runs/{live['run_id']}")).json()
            await replay(run, "SCEN0001", "CA0001", 10, "CU0001")
            current = (await run.get("/api/dev/runs/current")).json()
            assert set(current) == {"scenario_id", "card_id", "delivered", "total", "running", "next_at"}
            assert (current["delivered"], current["total"], current["running"]) == (10, 10, False)

            # read-only, whatever ONEGUARD_ALLOW_RUNS says
            monkeypatch.setenv("ONEGUARD_ALLOW_RUNS", "false")
            runs_before = dict(run.fake.runs)
            again = await run.get("/api/dev/runs/current")
            assert again.status_code == 200 and again.json() == current
            assert run.fake.runs == runs_before

    asyncio.run(scenario())


def test_d7_reads_the_stored_newest_run_after_a_restart(db_url: str) -> None:
    """A live run stored by an earlier process is still D7's answer (worker_ok false)."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            await confirm_form(run)
            live = (await run.post("/api/dev/runs", json={"scenario_id": "SCEN0000", "card_id": "CA0001"})).json()

            async def decided() -> bool:
                return (await run.get("/api/dev/runs/current")).json()["decided"] == 1

            await until(decided)
        async with running(db_url) as run:  # no worker now
            current = (await run.get("/api/dev/runs/current")).json()
            assert (current["run_id"], current["decided"], current["worker_ok"]) == (live["run_id"], 1, False)

    asyncio.run(scenario())
