"""``make demo-live SCEN=<scenario id>``: one served scenario end to end, decided by the
OneGuard server, never by this process.

The server at ``--api`` (``ONEGUARD_API``, default the cloud app) is the one decider: its
worker polls Viseca. This command only drives the server's API and reads it:

1. ``GET /healthz``; no server answering → the local fallback below, said out loud;
2. D8 ``GET /api/dev/scenarios``: the scenario's instruction and the customer and card the
   platform runs it on (none yet for a scenario never run: then ``--card``, else the
   bootstrap profile's card; the server moves the policy to the run's card at D3);
3. D7 and D8: while the newest run is unfinished, or the scenario has a run still running
   or with purchases open at the platform, that run is named and nothing is changed
   (exit 1), unless ``--force`` (then D3 gets ``force: true``);
4. C1 compile the instruction on that card, C2 confirm what it proposes;
5. D3 start the run (409 ``run_active`` → exit 1), then, before the first decision, print
   ``Sign in as <name> (<customer id>, card <card id>)`` from the run's fixture profile;
6. follow D4 and the customer's decisions (C6) read-only, one line per decision and per
   step-up outcome, until the run is done.

Nothing here answers a step-up: the customer does, in the app (CLAUDE.md rule 6).

Local fallback (no server at ``--api``): the old behaviour, a worker in this process
(bootstrap, served tables synced, long-poll), compile, create and confirm the mandate at
Viseca, start and tail the run. It needs ``VISECA_API_KEY`` (exit 2 without it) and must
never run next to a server that decides the same team's requests.

    python -m oneguard.viseca.demo --scenario <scenario id> [--card <card id>] [--api <url>] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import Engine, select

from oneguard.engine import stubs
from oneguard.engine.types import CompiledDraft, Policy, Rule
from oneguard.llm.provider import Provider, get_provider
from oneguard.store import seed as seed_module
from oneguard.store.db import get_engine, init_db, session
from oneguard.store.schema import ScenarioCatalogue
from oneguard.viseca.client import (
    API_KEY_ENV,
    RUNS_DISABLED_MESSAGE,
    VisecaClient,
    runs_allowed,
    store_sink,
)
from oneguard.viseca.worker import (
    POLL_WAIT_S,
    VisecaWorker,
    first_value,
    run_total,
    served_profile,
    walk_json,
)

log = logging.getLogger(__name__)

API_ENV = "ONEGUARD_API"
DEFAULT_API = "https://oneguard.fly.dev"
API_TIMEOUT_S = 30.0
"""C1 compiles within 10 s and D3 waits up to 15 s on the platform; a slow network on top."""

MISSING_KEY = (
    f"{API_KEY_ENV} is not set. The live demo talks to the Viseca sandbox and needs your "
    "team's bearer key.\nExport it in this shell (it stays server-side, never in git) and "
    f"rerun:\n  export {API_KEY_ENV}=<team key>\n  make demo-live SCEN=<scenario id>"
)


STANDBY_MESSAGE = (
    "Another OneGuard worker is already polling Viseca on this store, so this demo would "
    "not decide anything. Start the run from that server (D3), or stop it first."
)


def rule_to_viseca(rule: Rule) -> dict[str, Any]:
    """A typed rule in Viseca's ``hard_rules`` format: unused optional fields omitted."""
    out: dict[str, Any] = {"field": rule.field, "operator": rule.operator, "value": rule.value}
    for key in ("currency", "scope", "period_days"):
        if getattr(rule, key) is not None:
            out[key] = getattr(rule, key)
    return out


def policy_from_draft(mandate_id: str, draft: CompiledDraft) -> Policy:
    return Policy(
        mandate_id=mandate_id,
        status="active",
        instruction=draft.instruction,
        rules=draft.rules,
        uncertainty_policy=draft.uncertainty_policy,
        requested_item=draft.requested_item,
        allowed_item_categories=draft.allowed_item_categories,
        blocked_item_categories=draft.blocked_item_categories,
        requires_known_shop=draft.requires_known_shop,
        nothing_extra=draft.nothing_extra,
        shop_type=draft.shop_type,
    )


def scenario_from_reference(reference: Any, scenario_id: str) -> tuple[str | None, str | None]:
    """(instruction, card id) for a scenario from ``/v1/reference-data``, if it lists them.

    The live sandbox lists scenarios in ``tables.scenario_catalogue`` (``scenario_id``,
    ``scenario_name``, ``cardholder_instruction``, ``event_count``; no card id).
    """
    tables = reference.get("tables") if isinstance(reference, dict) else None
    catalogue = tables.get("scenario_catalogue") if isinstance(tables, dict) else None
    for row in catalogue if isinstance(catalogue, list) else []:
        if isinstance(row, dict) and row.get("scenario_id") == scenario_id:
            instruction = row.get("cardholder_instruction")
            if isinstance(instruction, str) and instruction:
                return instruction, row.get("card_id")
    for _, value, _ in walk_json(reference):
        if isinstance(value, dict) and value.get("scenario_id") == scenario_id:
            instruction = value.get("cardholder_instruction") or value.get("instruction")
            if isinstance(instruction, str) and instruction:
                return instruction, first_value(value, "card_id")
    return None, None


def scenario_from_store(db: Engine, scenario_id: str) -> str | None:
    with session(db) as s:
        return s.scalar(
            select(ScenarioCatalogue.cardholder_instruction).where(
                ScenarioCatalogue.scenario_id == scenario_id
            )
        )


def _line(decision: Mapping[str, Any]) -> str:
    """One decision (the C6 ``Decision`` as JSON): outcome, amount, reason codes, message."""
    state = str(decision.get("decision"))
    uncertain = decision.get("uncertain_outcome")
    if uncertain and uncertain != "pending":
        state += f"/{uncertain}"
    elif decision.get("status") == "pending_human" and decision.get("deadline_at"):
        deadline = datetime.fromisoformat(str(decision["deadline_at"]))
        state += f" (waiting until {deadline:%H:%M:%S})"
    codes = ",".join(decision.get("reason_codes") or [])
    amount = float(decision.get("billing_amount_chf") or 0)
    return f"  {decision.get('authorization_id')}  {state:<22} CHF {amount:>8.2f}  [{codes}] {decision.get('message')}"


# Through the server's API ---------------------------------------------------------------


class ApiRefused(Exception):
    """The server answered an error: ``code`` from its §3.8 envelope, ``message`` for people."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


async def _call(http: httpx.AsyncClient, method: str, path: str, body: Any = None) -> Any:
    reply = await http.request(method, path, json=body)
    if reply.is_success:
        return reply.json() if reply.content else None
    try:
        error = reply.json()["error"]
        code, message = str(error["code"]), str(error["message"])
    except (ValueError, KeyError, TypeError):
        code, message = "http_error", reply.text[:300]
    raise ApiRefused(reply.status_code, code, message)


async def server_health(http: httpx.AsyncClient) -> dict[str, Any] | None:
    """The ``/healthz`` of a OneGuard server at the client's base URL (healthy or degraded),
    or None when nothing answers there, or something that is not OneGuard."""
    try:
        reply = await http.get("/healthz")
        body = reply.json()
    except (httpx.HTTPError, ValueError):
        return None
    return body if isinstance(body, dict) and isinstance(body.get("worker"), dict) else None


def _card_for(scenario: Mapping[str, Any], scenarios: list[Mapping[str, Any]], card_id: str | None) -> str | None:
    """The card to confirm the policy on: ``--card``, the scenario's own, else the bootstrap
    profile's (the server moves the policy to the run's card at D3)."""
    profile = scenario.get("profile")
    if card_id:
        return card_id
    if profile:
        return str(profile["card_id"])
    bootstrap = [s["profile"] for s in scenarios if s.get("profile") and s["profile"].get("source") == "bootstrap"]
    return str(bootstrap[0]["card_id"]) if bootstrap else None


async def _refuse_while_running(
    http: httpx.AsyncClient, scenario: Mapping[str, Any], base: str, out: Callable[[str], None]
) -> bool:
    """True (and says why) when a run is in progress: the newest run (D7) is unfinished, or
    the scenario has a run still running or with purchases open at the platform (D8)."""
    try:
        current = await _call(http, "GET", "/api/dev/runs/current")
    except ApiRefused as exc:
        if exc.status != 404:
            raise
        current = None
    if current and current.get("run_id") and current.get("state") in ("starting", "running"):
        out(
            f"Run {current['run_id']} ({current['scenario_id']}) is still running at {base}: "
            f"{current['decided']}/{current['total']} decided, {current['pending_human']} waiting for "
            "the customer. Nothing was changed; start the next run when it is done, or pass --force."
        )
        return True
    if scenario.get("active_run_id"):
        out(
            f"{scenario['scenario_id']} already has run {scenario['active_run_id']} in progress (running, "
            "or purchases still open at the platform). Nothing was changed; pass --force to start "
            "another anyway."
        )
        return True
    return False


async def run_via_api(
    http: httpx.AsyncClient,
    scenario_id: str,
    *,
    card_id: str | None = None,
    force: bool = False,
    out: Callable[[str], None] = print,
    max_seconds: float = 900.0,
    poll_s: float = 1.0,
) -> int:
    """Start one scenario through the server's API and follow it read-only.

    0 when the run finished, 1 when it could not start or did not finish in time, 2 when
    the server has runs switched off.
    """
    base = str(http.base_url).rstrip("/")
    try:
        listed = (await _call(http, "GET", "/api/dev/scenarios"))["scenarios"]
        scenario = next((s for s in listed if s["scenario_id"] == scenario_id), None)
        if scenario is None:
            out(f"No scenario {scenario_id} at {base}.")
            return 1
        if not scenario["served"]:
            out(f"The platform does not serve {scenario_id} now; nothing was started.")
            return 1
        if not force and await _refuse_while_running(http, scenario, base, out):
            return 1
        card = _card_for(scenario, listed, card_id)
        if card is None:
            out(f"Nobody knows yet which card {scenario_id} runs on; pass --card <card id>.")
            return 1
        if scenario["profile"] is None:
            out(
                f"{scenario_id} has not run yet, so its card is not known: the policy starts on card "
                f"{card} and moves to the card the platform names when the run starts."
            )
        instruction = scenario["cardholder_instruction"]
        out(f"Instruction: {instruction}")
        draft = await _call(http, "POST", f"/api/cards/{card}/policy-drafts", {"instruction": instruction})
        out(f"Compiled ({draft.get('compiler', 'unknown')}), uncertainty: {draft['uncertainty_policy']}")
        for check in draft["checks"]:
            out(f"  check {check['id']}: {check['text']} [{check['source']}]")
        for question in draft["open_questions"]:
            out(f"  open question: {question}")
        mandate = await _call(
            http,
            "POST",
            f"/api/policy-drafts/{draft['draft_id']}/confirm",
            {
                "checks": draft["checks"],
                "uncertainty_policy": draft["uncertainty_policy"],
                "open_questions": draft["open_questions"],
            },
        )
        out(f"Policy {mandate['mandate_id']} confirmed on card {mandate['card_id']}")
        start: dict[str, Any] = {"scenario_id": scenario_id, "card_id": card}
        if force:
            start["force"] = True
        live = await _call(http, "POST", "/api/dev/runs", start)
    except ApiRefused as exc:
        out(f"{base} refused: {exc.message} ({exc.code})")
        return 2 if exc.code == "runs_disabled" else 1
    except httpx.HTTPError as exc:
        out(f"{base} did not answer: {type(exc).__name__}")
        return 1

    run_id, customer_id = live["run_id"], live.get("customer_id")
    if customer_id:
        out(f"Sign in as {live.get('customer_name') or customer_id} ({customer_id}, card {live['card_id']})")
    else:
        out(f"Sign in as the holder of card {live['card_id']}")
    out(f"Run {run_id} started at {base}")
    return await follow(http, run_id, customer_id, out=out, max_seconds=max_seconds, poll_s=poll_s)


async def follow(
    http: httpx.AsyncClient,
    run_id: str,
    customer_id: str | None,
    *,
    out: Callable[[str], None] = print,
    max_seconds: float = 900.0,
    poll_s: float = 1.0,
) -> int:
    """Print the run's progress (D4) and each decision of its customer (C6) until it is done.

    Only reads. A decision is printed when it appears and again when its state changes (a
    step-up answered or expired). The customer's decisions from before the run are skipped.
    """
    before = {d["authorization_id"] for d in await _decisions(http, customer_id)} if customer_id else set()
    seen: dict[str, tuple[Any, ...]] = {}
    printed: dict[str, Mapping[str, Any]] = {}
    last: tuple[Any, ...] | None = None
    status: Mapping[str, Any] = {}
    deadline = time.monotonic() + max_seconds
    while True:
        try:
            status = await _call(http, "GET", f"/api/dev/runs/{run_id}")
            decisions = await _decisions(http, customer_id) if customer_id else []
        except (ApiRefused, httpx.HTTPError) as exc:
            out(f"reading the run failed: {exc}")
            decisions = []
        for d in reversed(decisions):
            key = (d.get("decision"), d.get("status"), d.get("uncertain_outcome"))
            if d["authorization_id"] in before or seen.get(d["authorization_id"]) == key:
                continue
            seen[d["authorization_id"]] = key
            printed[d["authorization_id"]] = d
            out(_line(d))
        if status:
            progress = (status["decided"], status["pending_human"], status["total"])
            if progress != last:
                out(f"progress: {status['decided']}/{status['total']} decided, "
                    f"{status['pending_human']} waiting for the customer")  # fmt: skip
                last = progress
            if status["state"] in ("done", "error"):
                break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_s)
    outcomes = Counter(
        f"{d['decision']}/{d['uncertain_outcome']}" if d.get("uncertain_outcome") else str(d["decision"])
        for d in printed.values()
    )
    out(f"Summary: {dict(outcomes)}")
    if status.get("state") != "done":
        out(f"Run not finished (state {status.get('state', 'unknown')}).")
        return 1
    return 0


async def _decisions(http: httpx.AsyncClient, customer_id: str) -> list[dict[str, Any]]:
    return (await _call(http, "GET", f"/api/customers/{customer_id}/decisions"))["decisions"]


# Local fallback ---------------------------------------------------------------------------


async def run_local(
    client: VisecaClient,
    scenario_id: str,
    *,
    db: Engine,
    card_id: str | None = None,
    provider: Provider | None = None,
    out: Callable[[str], None] = print,
    max_seconds: float = 900.0,
    poll_wait_s: float = POLL_WAIT_S,
    **worker_options: Any,
) -> int:
    """The local fallback: a worker here, then compile, confirm, run and tail one scenario.
    0 when the run finished in time."""
    provider = provider or get_provider()
    worker = VisecaWorker(client, db=db, provider=provider, poll_wait_s=poll_wait_s, **worker_options)
    await worker.start()
    try:
        if worker.status().state == "standby":
            out(STANDBY_MESSAGE)
            return 1
        instruction, served_card = scenario_from_reference(worker.reference_data, scenario_id)
        instruction = instruction or await asyncio.to_thread(scenario_from_store, db, scenario_id)
        if not instruction:
            out(f"No instruction found for scenario {scenario_id}.")
            return 1
        profile = served_profile(worker.bootstrap)
        card = card_id or served_card or (profile.card_id if profile else "")
        out(f"Instruction: {instruction}")

        compile_instruction = stubs.ACTIVE["compile_instruction"]
        draft: CompiledDraft = await asyncio.to_thread(
            compile_instruction, instruction, worker.history, card, provider
        )
        out(f"Compiled ({draft.compiler}), uncertainty: {draft.uncertainty_policy}")
        for rule in draft.rules:
            out(f"  check {rule.id}: {rule.text} [{rule.source}]")
        for question in draft.open_questions:
            out(f"  open question: {question}")

        created = await client.create_mandate(
            instruction,
            [rule_to_viseca(r) for r in draft.rules],
            draft.uncertainty_policy,
            guidance=[r.text for r in draft.rules],
            open_questions=draft.open_questions,
        )
        confirmed = await client.confirm_mandate(created["draft_id"])
        mandate_id = confirmed["mandate_id"]
        worker.bind_policy(mandate_id, policy_from_draft(mandate_id, draft))
        out(f"Mandate {mandate_id} confirmed")

        worker.add_listener(lambda decision: out(_line(decision.model_dump(mode="json"))))
        started = await client.create_run(scenario_id, mandate_id)
        run_id = started["run_id"]
        worker.track_run(
            run_id, scenario_id=scenario_id, viseca_mandate_id=mandate_id, total=run_total(started)
        )
        out(f"Run {run_id} started")

        deadline = time.monotonic() + max_seconds
        last = None
        while time.monotonic() < deadline:
            status = worker.run_status(run_id)
            if status is not None:
                progress = (status.delivered, status.decided, status.pending_human, status.total)
                if progress != last:
                    out(
                        f"progress: {status.decided}/{status.total} decided, "
                        f"{status.pending_human} waiting for the customer"
                    )
                    last = progress
                if status.state in ("done", "error"):
                    break
            await asyncio.sleep(0.2)
        status = worker.run_status(run_id)
        entries = await worker.ledger_entries(list(worker.source_ids))
        outcomes = Counter(
            f"step_up/{e.uncertain_outcome}" if e.uncertain_outcome else e.outcome for e in entries
        )
        out(f"Summary: {dict(outcomes)}")
        if status is None or status.state != "done":
            out(f"Run not finished (state {status.state if status else 'unknown'}).")
            return 1
        return 0
    finally:
        await worker.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True, help="scenario id from the catalogue")
    parser.add_argument(
        "--card", help="card to confirm the policy on (default: the scenario's, else the bootstrap profile's)"
    )
    parser.add_argument(
        "--api",
        default=os.environ.get(API_ENV, "").strip() or DEFAULT_API,
        help=f"the OneGuard server that decides (default: ${API_ENV}, else {DEFAULT_API})",
    )
    parser.add_argument(
        "--force", action="store_true", help="start even while a run of this scenario (or another) is in progress"
    )
    parser.add_argument("--max-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not runs_allowed():
        print(RUNS_DISABLED_MESSAGE, file=sys.stderr)
        return 2
    return asyncio.run(
        live(args.scenario, api_base=args.api, card_id=args.card, force=args.force, max_seconds=args.max_seconds)
    )


async def live(
    scenario_id: str,
    *,
    api_base: str,
    card_id: str | None = None,
    force: bool = False,
    max_seconds: float = 900.0,
    out: Callable[[str], None] = print,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """The server at ``api_base`` decides; only when none answers does a local worker."""
    async with httpx.AsyncClient(base_url=api_base, timeout=API_TIMEOUT_S, transport=transport) as http:
        health = await server_health(http)
        if health is not None:
            if not health["worker"].get("configured"):
                out(
                    f"OneGuard at {api_base} is not connected to the payment platform (no worker), "
                    "so it cannot run a scenario; nothing was started."
                )
                return 1
            out(f"OneGuard at {api_base} decides this run; this terminal only starts and follows it.")
            return await run_via_api(
                http, scenario_id, card_id=card_id, force=force, out=out, max_seconds=max_seconds
            )
    out(
        f"No OneGuard server answers at {api_base}. Falling back to a local worker in this "
        "process: it decides this run itself, so no server may be polling the same team now."
    )
    return await _run_local_main(scenario_id, card_id=card_id, max_seconds=max_seconds, out=out)


async def _run_local_main(
    scenario_id: str, *, card_id: str | None, max_seconds: float, out: Callable[[str], None]
) -> int:
    if not os.environ.get(API_KEY_ENV, "").strip():
        print(MISSING_KEY, file=sys.stderr)
        return 2
    db = get_engine()
    await asyncio.to_thread(init_db, db)
    with session(db) as s:
        seeded = seed_module.history_row_count(s) > 0
    if not seeded:
        log.info("store is empty; seeding the reference tables first")
        await asyncio.to_thread(seed_module.run, engine=db)
    async with VisecaClient(sink=store_sink(db)) as client:
        return await run_local(client, scenario_id, db=db, card_id=card_id, max_seconds=max_seconds, out=out)


if __name__ == "__main__":
    sys.exit(main())
