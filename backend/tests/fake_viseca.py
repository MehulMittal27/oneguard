"""An in-process fake of the Viseca sandbox for worker tests.

Implements every endpoint in vendor/viseca-2026/technical_details.md ("All API calls in
one place") over the offline replay events of ``oneguard.replay.events`` (P5), with the
live-run behaviour the worker depends on:

- a run queues its purchases one at a time: the next is queued when the previous gets a
  decision (or misses its deadline, which the platform records as a decline);
- ``deadline_at`` is set when a request is queued (``decision_deadline_s``), live ids are
  fresh per run and ``related_authorization_id`` is rewritten to the live id;
- ``context.approved_spend_in_period_chf`` is recomputed from the run's approvals;
- step-ups wait for ``/resolve``; nothing expires them here, so the worker must;
- ``/v1/reference-data`` serves ``tables.fx_rates`` as the rows of ``data/fx_rates.csv``;
- knobs for redelivery, corrupt events, a served history file with another hash or fx
  rates that differ, and a context / event-feed that disagrees with the worker.

Responses are plain JSON objects; errors use the ``{"error": {"code", "message"}}``
envelope. Everything is on the real clock except the purchases' simulated timestamps.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from oneguard.replay.events import (
    Pack,
    RunDecision,
    build_events,
    data_dir,
    with_run_context,
)

MANDATE_RULE_KEYS = {"field", "operator", "value", "currency", "scope", "period_days"}
DECISION_KEYS = {
    "authorization_id",
    "decision",
    "reason_codes",
    "customer_message",
    "evidence",
    "engine_version",
}
RESOLVE_KEYS = {"decision", "customer_message", "evidence"}


@dataclass
class FakeConfig:
    api_key: str = "fake-team-key"
    decision_deadline_s: float = 8.0
    human_window_s: float = 120.0
    max_wait_s: float = 25.0
    redeliver: frozenset[str] = frozenset()
    """Source ids delivered a second time after their first decision."""
    corrupt: frozenset[str] = frozenset()
    """Source ids delivered with ``merchant`` missing (fails the event schema)."""
    context_spend_offset: float = 0.0
    """Added to ``approved_spend_in_period_chf`` so it disagrees with the worker."""
    feed_status_override: dict[str, str] = field(default_factory=dict)
    """Source id → status the event feed reports for it, whatever really happened."""
    history_csv: str | None = None
    fx_rates: list[dict[str, Any]] | None = None
    """``tables.fx_rates`` rows served instead of data/fx_rates.csv (the live sandbox
    serves them under ``tables``; the rate's JSON type was not recorded, a number is assumed)."""
    """History file served instead of data/authorization_history.csv."""


@dataclass
class FakeAuth:
    live_id: str
    run_id: str
    source_id: str
    template: dict[str, Any]
    status: str = "waiting"  # waiting, queued, delivered, pending, approved, declined
    queued_at: datetime | None = None
    deadline_at: datetime | None = None
    deliveries: int = 0
    redeliver_pending: bool = False
    event: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    accepted_at: datetime | None = None
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    auto_declined: bool = False


@dataclass
class FakeRun:
    run_id: str
    scenario_id: str
    mandate: dict[str, Any]
    auths: list[FakeAuth]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FakeViseca:
    """State plus the FastAPI ``app``. Tests read the state to assert what was posted."""

    def __init__(self, config: FakeConfig | None = None) -> None:
        self.config = config or FakeConfig()
        self.pack = Pack.load()
        self.history_csv = self.config.history_csv or (data_dir() / "authorization_history.csv").read_text(
            encoding="utf-8"
        )
        with (data_dir() / "fx_rates.csv").open(encoding="utf-8", newline="") as f:
            pack_rates = [{**row, "rate": float(row["rate"])} for row in csv.DictReader(f)]
        self.fx_rates = self.config.fx_rates if self.config.fx_rates is not None else pack_rates
        self.reset()
        self.app = self._build_app()

    def reset(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.mandates: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, FakeRun] = {}
        self.auths: dict[str, FakeAuth] = {}
        self.feed: list[dict[str, Any]] = []
        self.polls = 0
        self.authorization_headers: list[str] = []

    # Queries used by tests ------------------------------------------------------------

    def by_source(self, run_id: str, source_id: str) -> FakeAuth:
        return next(a for a in self.runs[run_id].auths if a.source_id == source_id)

    def all_auths(self) -> list[FakeAuth]:
        return [a for run in self.runs.values() for a in run.auths]

    # Run mechanics ----------------------------------------------------------------------

    def _approved_spend(self, run: FakeRun, before: FakeAuth) -> float:
        at = datetime.fromisoformat(before.template["authorization"]["timestamp"])
        days = [r.get("period_days") for r in run.mandate["hard_rules"] if r.get("scope") == "period"]
        days = [d for d in days if d]
        start = at - timedelta(days=min(days)) if days else None
        total = 0.0
        for auth in run.auths:
            ts = datetime.fromisoformat(auth.template["authorization"]["timestamp"])
            if auth.status == "approved" and ts < at and (start is None or ts >= start):
                total += auth.template["authorization"]["billing_amount_chf"]
        return round(total, 2)

    def _queue(self, run: FakeRun, auth: FakeAuth) -> None:
        now = _now()
        auth.status = "queued"
        auth.queued_at = now
        auth.deadline_at = now + timedelta(seconds=self.config.decision_deadline_s)
        decided = [
            RunDecision(
                authorization_id=a.live_id,
                timestamp=a.template["authorization"]["timestamp"],
                merchant_id=a.template["authorization"]["merchant"]["merchant_id"],
                billing_amount_chf=a.template["authorization"]["billing_amount_chf"],
                status=a.status,
            )
            for a in run.auths
            if a.status in ("pending", "approved", "declined")
        ]
        event = with_run_context(copy.deepcopy(auth.template), decided)
        event["deadline_at"] = _iso(auth.deadline_at)
        event["runtime"]["received_at"] = _iso(now)
        spend = self._approved_spend(run, auth) + self.config.context_spend_offset
        event["context"]["approved_spend_in_period_chf"] = round(spend, 2)
        auth.event = event

    def _queue_next(self, run: FakeRun, after: FakeAuth) -> None:
        index = run.auths.index(after)
        if index + 1 < len(run.auths) and run.auths[index + 1].status == "waiting":
            self._queue(run, run.auths[index + 1])

    def _feed_event(self, auth: FakeAuth, kind: str, status: str) -> None:
        status = self.config.feed_status_override.get(auth.source_id, status)
        self.feed.append(
            {
                "cursor": len(self.feed) + 1,
                "type": kind,
                "run_id": auth.run_id,
                "authorization_id": auth.live_id,
                "status": status,
                "occurred_at": _iso(_now()),
            }
        )

    def _tick(self) -> None:
        now = _now()
        for run in self.runs.values():
            for auth in run.auths:
                if auth.status in ("queued", "delivered") and auth.deadline_at and now > auth.deadline_at:
                    auth.status = "declined"
                    auth.auto_declined = True
                    self._feed_event(auth, "authorization.expired", "declined")
                    self._queue_next(run, auth)

    def _next_ready(self) -> FakeAuth | None:
        ready = [
            a
            for a in self.all_auths()
            if a.status == "queued" or (a.redeliver_pending and a.status != "waiting")
        ]
        return min(ready, key=lambda a: a.queued_at or _now()) if ready else None

    def _run_view(self, run: FakeRun) -> dict[str, Any]:
        counts = {
            "total": len(run.auths),
            "queued": sum(a.status == "queued" for a in run.auths),
            "delivered": sum(a.deliveries > 0 for a in run.auths),
            "decided": sum(bool(a.decisions) or a.auto_declined for a in run.auths),
            "pending_human": sum(a.status == "pending" for a in run.auths),
            "final": sum(a.status in ("approved", "declined") for a in run.auths),
        }
        done = counts["final"] == counts["total"]
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "mandate_id": run.mandate["mandate_id"],
            "status": "completed" if done else "running",
            "counters": counts,
        }

    # App --------------------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def bearer(request: Request, call_next: Any) -> Response:
            header = request.headers.get("authorization", "")
            fake.authorization_headers.append(header)
            if request.url.path != "/healthz" and header != f"Bearer {fake.config.api_key}":
                return _error(401, "unauthorized", "missing or invalid bearer key")
            return await call_next(request)

        @app.get("/healthz")
        async def healthz() -> dict[str, Any]:
            return {"status": "ok", "version": "fake-viseca"}

        @app.get("/v1/bootstrap")
        async def bootstrap() -> dict[str, Any]:
            return {
                "api_version": "fake",
                "data_version": "saw26",
                "scenarios": fake.pack.scenario_ids(),
                "timeouts": {
                    "decision_deadline_seconds": fake.config.decision_deadline_s,
                    "human_window_seconds": fake.config.human_window_s,
                    "long_poll_max_wait_seconds": 25,
                },
                "limits": {"max_active_runs": 10},
                "features": {"team_reset": True},
            }

        @app.get("/v1/reference-data")
        async def reference_data() -> dict[str, Any]:
            text = fake.history_csv
            return {
                "scenarios": [
                    {
                        "scenario_id": s["scenario_id"],
                        "scenario_name": s["scenario_name"],
                        "cardholder_instruction": s["cardholder_instruction"],
                        "event_count": int(s["event_count"]),
                    }
                    for s in fake.pack.scenarios.values()
                ],
                "tables": {"fx_rates": fake.fx_rates},
                "files": {
                    "authorization_history": {
                        "url": "/v1/reference-data/authorization-history.csv",
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "rows": text.count("\n") - 1,
                    }
                },
            }

        @app.get("/v1/reference-data/authorization-history.csv")
        async def history_csv() -> PlainTextResponse:
            return PlainTextResponse(fake.history_csv, media_type="text/csv")

        @app.post("/v1/mandates")
        async def create_mandate(request: Request) -> Response:
            body = await request.json()
            missing = {"instruction", "hard_rules", "uncertainty_policy"} - set(body)
            if missing:
                return _error(422, "validation", f"missing {sorted(missing)}")
            if body["uncertainty_policy"] not in ("ask", "decline", "approve"):
                return _error(422, "validation", "bad uncertainty_policy")
            for rule in body["hard_rules"]:
                if set(rule) - MANDATE_RULE_KEYS or not {"field", "operator", "value"} <= set(rule):
                    return _error(422, "validation", "bad rule")
            draft_id = "draft_" + secrets.token_hex(4)
            draft = {
                "draft_id": draft_id,
                "status": "draft",
                "instruction": body["instruction"],
                "hard_rules": body["hard_rules"],
                "uncertainty_policy": body["uncertainty_policy"],
                "guidance": body.get("guidance", []),
                "open_questions": body.get("open_questions", []),
            }
            fake.drafts[draft_id] = draft
            return JSONResponse(draft, status_code=201)

        @app.post("/v1/mandates/{draft_id}/confirm")
        async def confirm(draft_id: str, request: Request) -> Response:
            draft = fake.drafts.get(draft_id)
            if draft is None:
                return _error(404, "not_found", "unknown draft")
            if (await request.json()).get("confirmed") is not True:
                return _error(422, "validation", "confirmed must be true")
            if draft["status"] != "draft":
                return _error(409, "already_confirmed", "draft already confirmed")
            draft["status"] = "confirmed"
            mandate_id = "TM" + secrets.token_hex(4).upper()
            mandate = {k: copy.deepcopy(v) for k, v in draft.items() if k not in ("draft_id", "status")}
            mandate.update(mandate_id=mandate_id, status="active", draft_id=draft_id)
            fake.mandates[mandate_id] = mandate
            return JSONResponse(mandate)

        @app.get("/v1/mandates/{mandate_id}")
        async def get_mandate(mandate_id: str) -> Response:
            mandate = fake.mandates.get(mandate_id)
            return JSONResponse(mandate) if mandate else _error(404, "not_found", "unknown mandate")

        @app.patch("/v1/mandates/{mandate_id}")
        async def patch_mandate(mandate_id: str, request: Request) -> Response:
            mandate = fake.mandates.get(mandate_id)
            if mandate is None:
                return _error(404, "not_found", "unknown mandate")
            if mandate["status"] != "active":
                return _error(409, "mandate_inactive", "mandate is not active")
            body = await request.json()
            if "hard_rules" in body and any(r not in body["hard_rules"] for r in mandate["hard_rules"]):
                return _error(409, "not_pure_addition", "existing rules must stay unchanged")
            policy = body.get("uncertainty_policy")
            if policy is not None and policy not in (mandate["uncertainty_policy"], "decline"):
                return _error(409, "loosening_not_allowed", "uncertainty_policy may only move to decline")
            for key in ("hard_rules", "uncertainty_policy", "guidance", "open_questions"):
                if key in body:
                    mandate[key] = body[key]
            return JSONResponse(mandate)

        @app.delete("/v1/mandates/{mandate_id}")
        async def delete_mandate(mandate_id: str) -> Response:
            mandate = fake.mandates.get(mandate_id)
            if mandate is None:
                return _error(404, "not_found", "unknown mandate")
            mandate["status"] = "revoked"
            return JSONResponse({"mandate_id": mandate_id, "status": "revoked"})

        @app.post("/v1/scenario-runs")
        async def create_run(request: Request) -> Response:
            body = await request.json()
            mandate = fake.mandates.get(body.get("mandate_id", ""))
            if mandate is None:
                return _error(404, "not_found", "unknown mandate")
            if mandate["status"] != "active":
                return _error(409, "mandate_inactive", "mandate is not active")
            scenario_id = body.get("scenario_id", "")
            if scenario_id not in fake.pack.scenarios:
                return _error(404, "not_found", "unknown scenario")
            run_id = "run_" + secrets.token_hex(4)
            live = {
                row["authorization_id"]: "lv_" + secrets.token_hex(6)
                for row in fake.pack.attempts_for(scenario_id)
            }
            templates = build_events(
                fake.pack, scenario_id, mandate_id=mandate["mandate_id"], live_id=live.__getitem__
            )
            for template in templates:
                snapshot = template["mandate"]
                snapshot["instruction"] = mandate["instruction"]
                snapshot["hard_rules"] = copy.deepcopy(mandate["hard_rules"])
                snapshot["uncertainty_policy"] = mandate["uncertainty_policy"]
            run = FakeRun(
                run_id=run_id,
                scenario_id=scenario_id,
                mandate=copy.deepcopy(mandate),
                auths=[
                    FakeAuth(
                        live_id=t["authorization"]["authorization_id"],
                        run_id=run_id,
                        source_id=t["authorization"]["source_authorization_id"],
                        template=t,
                    )
                    for t in templates
                ],
            )
            fake.runs[run_id] = run
            fake.auths.update({a.live_id: a for a in run.auths})
            fake._queue(run, run.auths[0])
            return JSONResponse(fake._run_view(run), status_code=201)

        @app.get("/v1/scenario-runs/{run_id}")
        async def get_run(run_id: str) -> Response:
            fake._tick()
            run = fake.runs.get(run_id)
            return JSONResponse(fake._run_view(run)) if run else _error(404, "not_found", "unknown run")

        @app.get("/v1/decision-requests/next")
        async def next_request(wait: float = 25) -> Response:
            fake.polls += 1
            until = _now() + timedelta(seconds=min(wait, fake.config.max_wait_s))
            while True:
                fake._tick()
                auth = fake._next_ready()
                if auth is not None:
                    break
                if _now() >= until:
                    return Response(status_code=204)
                await asyncio.sleep(0.01)
            assert auth.event is not None
            if auth.redeliver_pending:
                auth.redeliver_pending = False
            else:
                auth.status = "delivered"
            auth.deliveries += 1
            data = copy.deepcopy(auth.event)
            if auth.source_id in fake.config.corrupt:
                del data["authorization"]["merchant"]
            return JSONResponse(
                {
                    "run_id": auth.run_id,
                    "event_id": f"evt_{auth.live_id}_{auth.deliveries}",
                    "type": "authorization.request",
                    "authorization_id": auth.live_id,
                    "status": "pending",
                    "occurred_at": _iso(_now()),
                    "data": data,
                }
            )

        @app.post("/v1/authorizations/{authorization_id}/decision")
        async def decision(authorization_id: str, request: Request) -> Response:
            fake._tick()
            auth = fake.auths.get(authorization_id)
            if auth is None:
                return _error(404, "not_found", "unknown authorization")
            body = await request.json()
            if set(body) - DECISION_KEYS or body.get("authorization_id") != authorization_id:
                return _error(422, "validation", "bad decision body")
            if body.get("decision") not in ("approve", "decline", "step_up"):
                return _error(422, "validation", "bad decision")
            if auth.decisions or auth.status not in ("delivered",):
                auth.decisions.append({**body, "rejected": True})
                return _error(409, "already_decided", f"authorization is {auth.status}")
            now = _now()
            auth.decisions.append(body)
            auth.accepted_at = now
            auth.status = {"approve": "approved", "decline": "declined", "step_up": "pending"}[body["decision"]]
            fake._feed_event(auth, "authorization.decided", auth.status)
            if auth.source_id in fake.config.redeliver and auth.deliveries == 1:
                auth.redeliver_pending = True
                auth.queued_at = now
            run = fake.runs[auth.run_id]
            fake._queue_next(run, auth)
            reply = {
                "authorization_id": authorization_id,
                "decision": body["decision"],
                "status": auth.status,
                "accepted_at": _iso(now),
            }
            return JSONResponse(reply)

        @app.post("/v1/authorizations/{authorization_id}/resolve")
        async def resolve(authorization_id: str, request: Request) -> Response:
            auth = fake.auths.get(authorization_id)
            if auth is None:
                return _error(404, "not_found", "unknown authorization")
            body = await request.json()
            if set(body) - RESOLVE_KEYS or body.get("decision") not in ("approve", "decline"):
                return _error(422, "validation", "bad resolve body")
            if auth.status != "pending":
                return _error(409, "not_awaiting_answer", f"authorization is {auth.status}")
            auth.resolutions.append({**body, "at": _now()})
            auth.status = "approved" if body["decision"] == "approve" else "declined"
            fake._feed_event(auth, "authorization.resolved", auth.status)
            return JSONResponse({"authorization_id": authorization_id, "status": auth.status})

        @app.get("/v1/authorizations")
        async def authorizations() -> dict[str, Any]:
            fake._tick()
            return {
                "authorizations": [
                    {"authorization_id": a.live_id, "run_id": a.run_id, "status": a.status}
                    for a in fake.all_auths()
                    if a.status != "waiting"
                ]
            }

        @app.get("/v1/events")
        async def events(since: int = 0) -> dict[str, Any]:
            fake._tick()
            items = [e for e in fake.feed if e["cursor"] > since]
            return {"events": items, "next_cursor": items[-1]["cursor"] if items else since}

        @app.post("/v1/team/reset")
        async def team_reset() -> dict[str, Any]:
            fake.reset()
            return {"reset": True}

        return app

