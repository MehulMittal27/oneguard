"""An in-process fake of the Viseca sandbox for worker tests.

Implements every endpoint in vendor/viseca-2026/technical_details.md ("All API calls in
one place") over the offline replay events of ``oneguard.replay.events`` (P5). Response
shapes, status values and error codes follow the live sandbox as captured on 24 Sep 2026
(docs/decisions.md); where the live run did not show a case (an ``approve`` reply, a
successful ``/resolve`` or reset) the shape is our best guess and says so. Live-run
behaviour the worker depends on:

- a run queues its purchases one at a time: the next is queued when the previous gets a
  decision (or misses its deadline, which the platform records as a decline);
- ``deadline_at`` is set when a request is queued (``decision_deadline_s``), live ids are
  fresh per run and ``related_authorization_id`` is rewritten to the live id;
- ``context.approved_spend_in_period_chf`` is recomputed from the run's approvals;
- a step-up is served again on every poll (envelope ``status: "pending_step_up"``, no
  long-poll wait) while it waits for ``/resolve``; queued requests are served first
  (the live run had only one purchase, so this order is assumed);
- the platform expires a step-up itself at ``step_up_expires_at`` (accepted time + the
  human window): decline, ``decision_source: "timeout"``; a later ``/resolve`` is 409
  ``authorization_not_pending``. Tests move that moment with ``platform_expiry_offset_s``
  and can have it happen just before a ``/resolve`` (``expire_before_resolve``);
- ``GET /v1/authorizations`` filters by ``run_id`` and ``status`` (as live) and ignores
  other parameters;
- ``/v1/reference-data`` serves no history-file hash;
- knobs for redelivery, corrupt events, a served history file that differs, a
  context / event-feed that disagrees with the worker, and whether team reset is enabled.

Errors use the ``{"error": {"code", "message", "details"?}}`` envelope. Everything is on
the real clock except the purchases' simulated timestamps.
"""

from __future__ import annotations

import asyncio
import copy
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
    """History file served instead of data/authorization_history.csv."""
    reset_enabled: bool = True
    """``features.reset``; the live sandbox has it off (403 ``reset_disabled``)."""
    platform_expiry_offset_s: float = 0.0
    """When the fake expires a waiting step-up, relative to its ``step_up_expires_at``:
    negative → before the worker's deadline check, positive → after it."""
    expire_before_resolve: frozenset[str] = frozenset()
    """Source ids the fake expires just before a ``/resolve`` for them is handled (after the
    worker read the step-up as still pending), so that ``/resolve`` gets a 409."""
    pending_serve_delay_s: float = 0.0
    """Delay before a ``pending_step_up`` envelope is returned, so it can arrive stale."""


@dataclass
class FakeAuth:
    live_id: str
    run_id: str
    source_id: str
    template: dict[str, Any]
    status: str = "waiting"  # waiting, queued, delivered, pending, approved, declined
    event_id: int = 0
    """Feed id of its ``authorization.request`` event, repeated in every envelope."""
    queued_at: datetime | None = None
    deadline_at: datetime | None = None
    deliveries: int = 0
    """Deliveries of the request to decide (not the re-serves of a waiting step-up)."""
    step_up_serves: int = 0
    """Times it was served again as ``pending_step_up``."""
    redeliver_pending: bool = False
    event: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    accepted_at: datetime | None = None
    expires_at: datetime | None = None
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    """Every /resolve attempt; a refused one carries ``rejected: True``."""
    auto_declined: bool = False
    platform_expired: bool = False
    platform_decision: dict[str, Any] | None = None
    """The last decision the platform recorded (ours, the customer's or its timeout)."""
    finalized_at: datetime | None = None


@dataclass
class FakeRun:
    run_id: str
    scenario_id: str
    mandate: dict[str, Any]
    auths: list[FakeAuth]


def _error(status: int, code: str, message: str, details: list[Any] | None = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status)


def _missing(*loc: str) -> JSONResponse:
    detail = {"type": "missing", "loc": ["body", *loc], "msg": "Field required", "input": None}
    return _error(422, "validation_error", "Request validation failed", [detail])


PLATFORM_STATUS = {
    "queued": "awaiting_decision",
    "delivered": "awaiting_decision",
    "pending": "pending_step_up",
    "approved": "approved",
    "declined": "declined",
}
"""Our internal auth status → the status the live sandbox reports."""


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
        self.reset()
        self.app = self._build_app()

    def reset(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.mandates: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, FakeRun] = {}
        self.auths: dict[str, FakeAuth] = {}
        self.feed: list[dict[str, Any]] = []
        self.resets: list[dict[str, Any]] = []
        self.authorization_reads: list[dict[str, Any]] = []
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
        auth.event_id = self._feed_add(
            auth.run_id, "authorization.request", auth.live_id, "awaiting_decision", event, now
        )

    def _queue_next(self, run: FakeRun, after: FakeAuth) -> None:
        index = run.auths.index(after)
        if index + 1 < len(run.auths) and run.auths[index + 1].status == "waiting":
            self._queue(run, run.auths[index + 1])

    def _feed_add(
        self,
        run_id: str,
        kind: str,
        live_id: str | None,
        status: str,
        data: dict[str, Any],
        at: datetime | None = None,
    ) -> int:
        event_id = len(self.feed) + 1
        self.feed.append(
            {
                "event_id": event_id,
                "type": kind,
                "run_id": run_id,
                "authorization_id": live_id,
                "status": status,
                "occurred_at": _iso(at or _now()),
                "data": copy.deepcopy(data),
            }
        )
        return event_id

    def _feed_decision(self, auth: FakeAuth, data: dict[str, Any]) -> None:
        auth.platform_decision = copy.deepcopy(data)
        if auth.status in ("approved", "declined"):
            auth.finalized_at = _now()
        status = self.config.feed_status_override.get(auth.source_id, PLATFORM_STATUS[auth.status])
        self._feed_add(auth.run_id, "authorization.decision", auth.live_id, status, data)
        run = self.runs[auth.run_id]
        if all(a.status in ("approved", "declined") for a in run.auths):
            self._feed_add(run.run_id, "scenario.completed", None, "completed", self._run_view(run))

    def _platform_decision(self, auth: FakeAuth, reason: str, message: str) -> None:
        self._feed_decision(
            auth,
            {
                "type": "authorization.decision",
                "authorization_id": auth.live_id,
                "decision": "decline",
                "reason_codes": [reason],
                "customer_message": message,
                "evidence": [],
                "engine_version": None,
                "decision_source": "timeout",
            },
        )

    def _expire_step_up(self, auth: FakeAuth) -> None:
        auth.status = "declined"
        auth.platform_expired = True
        self._platform_decision(auth, "step_up_expired", "The confirmation window expired.")

    def _tick(self) -> None:
        now = _now()
        offset = timedelta(seconds=self.config.platform_expiry_offset_s)
        for run in self.runs.values():
            for auth in run.auths:
                if auth.status in ("queued", "delivered") and auth.deadline_at and now > auth.deadline_at:
                    auth.status = "declined"
                    auth.auto_declined = True
                    self._platform_decision(auth, "decision_timeout", "No decision arrived in time.")
                    self._queue_next(run, auth)
                elif auth.status == "pending" and auth.expires_at and now >= auth.expires_at + offset:
                    self._expire_step_up(auth)

    def _next_ready(self) -> FakeAuth | None:
        """A request to decide first; else a step-up still waiting for its answer."""
        auths = self.all_auths()
        ready = [a for a in auths if a.status == "queued" or (a.redeliver_pending and a.status != "waiting")]
        if ready:
            return min(ready, key=lambda a: a.queued_at or _now())
        waiting = [a for a in auths if a.status == "pending"]
        return min(waiting, key=lambda a: a.event_id) if waiting else None

    def _run_view(self, run: FakeRun) -> dict[str, Any]:
        final = sum(a.status in ("approved", "declined") for a in run.auths)
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "mandate_id": run.mandate["mandate_id"],
            "status": "completed" if final == len(run.auths) else "running",
            "fixture_profiles": [
                {
                    "profile_id": run.auths[0].template["authorization"]["profile_id"],
                    "customer_id": run.auths[0].template["mandate"]["customer_id"],
                    "card_id": run.auths[0].template["authorization"]["card_id"],
                }
            ],
            "generated_event_count": len(run.auths),
            "delivered_event_count": sum(a.deliveries > 0 for a in run.auths),
            "finalized_event_count": final,
            "processed_event_count": sum(bool(a.decisions) or a.auto_declined for a in run.auths),
            "pending_event_count": sum(a.status == "pending" for a in run.auths),
            "queued_event_count": sum(a.status == "queued" for a in run.auths),
            "platform_rejected_count": 0,
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
            return {"status": "ok", "service": "fake-viseca", "api_version": "0.1.0", "pack_version": "saw26"}

        def catalogue() -> list[dict[str, Any]]:
            return [
                {
                    "scenario_id": s["scenario_id"],
                    "scenario_name": s["scenario_name"],
                    "cardholder_instruction": s["cardholder_instruction"],
                    "event_count": int(s["event_count"]),
                }
                for s in fake.pack.scenarios.values()
            ]

        @app.get("/v1/bootstrap")
        async def bootstrap() -> dict[str, Any]:
            return {
                "type": "bootstrap",
                "api_version": "0.1.0",
                "pack_version": "saw26",
                "team_id": "team-fake",
                "profile": {"profile_id": "PROFILE_AUTH0001", "scenario_id": "SCEN0000"},
                "scenarios": catalogue(),
                "limits": {
                    "decision_timeout_seconds": fake.config.decision_deadline_s,
                    "step_up_timeout_seconds": fake.config.human_window_s,
                    "long_poll_max_seconds": 25,
                },
                "features": {"reset": fake.config.reset_enabled},
            }

        @app.get("/v1/reference-data")
        async def reference_data() -> dict[str, Any]:
            return {
                "type": "reference_data",
                "pack_version": "saw26",
                "classification": "SYNTHETIC TEST DATA",
                "tables": {
                    "scenario_catalogue": catalogue(),
                    "fx_rates": [
                        {"from_currency": c, "to_currency": "CHF"} for c in ("CHF", "EUR", "GBP", "USD")
                    ],
                },
                "history": {
                    "path": "/v1/reference-data/authorization-history.csv",
                    "rows": fake.history_csv.count("\n") - 1,
                    "format": "csv",
                },
                "runtime": {"scenario_ids": fake.pack.scenario_ids()},
            }

        @app.get("/v1/reference-data/authorization-history.csv")
        async def history_csv() -> PlainTextResponse:
            return PlainTextResponse(fake.history_csv, media_type="text/csv")

        @app.post("/v1/mandates")
        async def create_mandate(request: Request) -> Response:
            body = await request.json()
            missing = {"instruction", "hard_rules", "uncertainty_policy"} - set(body)
            if missing:
                return _missing(min(missing))
            if body["uncertainty_policy"] not in ("ask", "decline", "approve"):
                return _error(422, "validation_error", "bad uncertainty_policy")
            for rule in body["hard_rules"]:
                if set(rule) - MANDATE_RULE_KEYS or not {"field", "operator", "value"} <= set(rule):
                    return _error(422, "validation_error", "bad rule")
            draft_id = "draft_" + secrets.token_hex(8)
            draft = {
                "draft_id": draft_id,
                "mandate_id": None,
                "status": "draft",
                "instruction": body["instruction"],
                "hard_rules": body["hard_rules"],
                "uncertainty_policy": body["uncertainty_policy"],
                "guidance": body.get("guidance", []),
                "open_questions": body.get("open_questions", []),
                "created_at": _iso(_now()),
            }
            fake.drafts[draft_id] = draft
            return JSONResponse({**draft, "requires_confirmation": True})

        @app.post("/v1/mandates/{draft_id}/confirm")
        async def confirm(draft_id: str, request: Request) -> Response:
            draft = fake.drafts.get(draft_id)
            if draft is None:
                return _error(404, "not_found", "unknown draft")
            if (await request.json()).get("confirmed") is not True:
                return _missing("confirmed")
            if draft["status"] != "draft":
                return _error(409, "already_confirmed", "draft already confirmed")
            draft["status"] = "confirmed"
            mandate_id = "TM" + secrets.token_hex(8)
            mandate = copy.deepcopy(draft)
            mandate.update(mandate_id=mandate_id, status="active")
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
            run_id = "run_" + secrets.token_hex(8)
            suffix = run_id[-8:]
            live = {
                row["authorization_id"]: f"{row['authorization_id']}-{suffix}"
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
            return JSONResponse(fake._run_view(run))

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
            assert auth.event is not None and auth.queued_at is not None
            status = "awaiting_decision"
            if auth.redeliver_pending:
                auth.redeliver_pending = False
                auth.deliveries += 1
            elif auth.status == "pending":
                status = "pending_step_up"
                auth.step_up_serves += 1
            else:
                auth.status = "delivered"
                auth.deliveries += 1
            data = copy.deepcopy(auth.event)
            if auth.source_id in fake.config.corrupt:
                del data["authorization"]["merchant"]
            if status == "pending_step_up" and fake.config.pending_serve_delay_s:
                await asyncio.sleep(fake.config.pending_serve_delay_s)
            return JSONResponse(
                {
                    "event_id": auth.event_id,
                    "type": "authorization.request",
                    "run_id": auth.run_id,
                    "authorization_id": auth.live_id,
                    "status": status,
                    "occurred_at": _iso(auth.queued_at),
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
                return _error(422, "validation_error", "bad decision body")
            if body.get("decision") not in ("approve", "decline", "step_up"):
                return _error(422, "validation_error", "bad decision")
            if auth.decisions or auth.status not in ("delivered",):
                auth.decisions.append({**body, "rejected": True})
                if auth.status == "pending":
                    return _error(
                        409,
                        "step_up_resolution_required",
                        "Resolve the pending step-up through the /resolve endpoint",
                    )
                if auth.status in ("approved", "declined"):
                    return _error(409, "authorization_finalized", "Authorization is already final")
                # not seen live: a decision for a request not yet delivered
                return _error(409, "authorization_not_delivered", f"authorization is {auth.status}")
            now = _now()
            auth.decisions.append(body)
            auth.accepted_at = now
            auth.status = {"approve": "approved", "decline": "declined", "step_up": "pending"}[body["decision"]]
            recorded = {**body, "decision_source": "team"}
            reply: dict[str, Any] = {
                "authorization_id": authorization_id,
                "status": PLATFORM_STATUS[auth.status],
                "decision": recorded,
            }
            if auth.status == "pending":
                auth.expires_at = now + timedelta(seconds=fake.config.human_window_s)
                reply["step_up_expires_at"] = _iso(auth.expires_at)
            fake._feed_decision(auth, recorded)
            if auth.source_id in fake.config.redeliver and auth.deliveries == 1:
                auth.redeliver_pending = True
                auth.queued_at = now
            run = fake.runs[auth.run_id]
            fake._queue_next(run, auth)
            return JSONResponse(reply)

        @app.post("/v1/authorizations/{authorization_id}/resolve")
        async def resolve(authorization_id: str, request: Request) -> Response:
            fake._tick()
            auth = fake.auths.get(authorization_id)
            if auth is None:
                return _error(404, "not_found", "unknown authorization")
            body = await request.json()
            if set(body) - RESOLVE_KEYS or body.get("decision") not in ("approve", "decline"):
                return _error(422, "validation_error", "bad resolve body")
            if auth.status == "pending" and auth.source_id in fake.config.expire_before_resolve:
                fake._expire_step_up(auth)
            now = _now()
            if auth.status != "pending":
                auth.resolutions.append({**body, "at": now, "rejected": True})
                return _error(409, "authorization_not_pending", "Authorization is not awaiting step-up")
            auth.resolutions.append({**body, "at": now})
            auth.status = "approved" if body["decision"] == "approve" else "declined"
            recorded = {"type": "authorization.decision", "authorization_id": authorization_id,
                        **body, "decision_source": "customer"}  # fmt: skip
            fake._feed_decision(auth, recorded)
            # not seen live (no resolve succeeded): the decision reply's shape is assumed
            return JSONResponse(
                {"authorization_id": authorization_id, "status": auth.status, "decision": recorded}
            )

        @app.get("/v1/authorizations")
        async def authorizations(run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
            fake._tick()
            fake.authorization_reads.append({"run_id": run_id, "status": status})
            listed = []
            for a in fake.all_auths():
                if a.status == "waiting" or (run_id is not None and a.run_id != run_id):
                    continue
                shown = PLATFORM_STATUS[a.status]
                if status is not None and shown != status:
                    continue
                decision = a.platform_decision
                listed.append(
                    {
                        "authorization_id": a.live_id,
                        "source_authorization_id": a.source_id,
                        "scenario_id": fake.runs[a.run_id].scenario_id,
                        "run_id": a.run_id,
                        "status": shown,
                        "decision": decision,
                        "decision_source": decision.get("decision_source") if decision else None,
                        "reason_codes": decision.get("reason_codes", []) if decision else [],
                        "occurred_at": _iso(a.queued_at) if a.queued_at else None,
                        "finalized_at": _iso(a.finalized_at) if a.finalized_at else None,
                        "authorization": copy.deepcopy(a.event["authorization"]) if a.event else None,
                    }
                )
            return listed

        @app.get("/v1/events")
        async def events(since: int = 0) -> dict[str, Any]:
            fake._tick()
            items = [e for e in fake.feed if e["event_id"] > since]
            return {"since": since, "next_cursor": items[-1]["event_id"] if items else since, "events": items}

        @app.post("/v1/team/reset")
        async def team_reset(request: Request) -> Response:
            body = await request.json() if await request.body() else None
            if not isinstance(body, dict):
                return _missing()
            if "confirmed" not in body:
                return _missing("confirmed")
            if not fake.config.reset_enabled:
                return _error(403, "reset_disabled", "Team reset is disabled during judging")
            resets = [*fake.resets, body]
            fake.reset()
            fake.resets = resets
            # not seen live (reset was disabled): the success body is assumed
            return JSONResponse({"reset": True})

        return app

