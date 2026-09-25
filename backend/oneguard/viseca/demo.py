"""``make demo-live SCEN=<scenario id>`` and ``make demo-offline``: drive the OneGuard server
that decides, never decide here.

The server at ``--api`` (``ONEGUARD_API_URL``, default the cloud app) is the one decider: its
worker polls Viseca. This command only drives the server's API and reads it. It starts
nothing, and no worker, unless that server answers ``/healthz``: a second decider next to
the cloud app would poll the same team's requests (docs/decisions.md).

demo-live:

1. ``GET /healthz``; no OneGuard server answering → a clear message, exit 1;
2. D8 ``GET /api/dev/scenarios``: the scenario's instruction and the customer and card the
   platform runs it on (none yet for a scenario never run: then ``--card``, else the
   bootstrap profile's card; the server moves the policy to the run's card at D3);
3. D7 and D8: while the newest run is unfinished, or the scenario has a run still running
   or with purchases open at the platform, that run is named and nothing is changed
   (exit 1), unless ``--force`` (then D3 gets ``force: true``);
4. enrol this terminal's device key on that card (its first device is enrolled at once;
   otherwise approve "demo-live on <host>" from the card's controller first,
   docs/passport.md), then C1 compile the instruction on that card, C2 confirm what it
   proposes, signed by that device;
5. D3 start the run (409 ``run_active`` → exit 1), then, before the first decision, print
   ``Sign in as <name> (<customer id>, card <card id>)`` from the run's fixture profile;
6. follow D4 and the customer's decisions (C6) read-only, one line per decision and per
   step-up outcome, until the run is done.

demo-offline (``--offline``): ``GET /healthz`` the same way, then D2 restarts the offline
replay of the scenario on the server, ``--speed-ms`` apart, on ``--card`` or else the card D9
(``GET /api/scenarios``, the store only) names for the scenario.

Nothing here answers a step-up: the customer does, in the app (CLAUDE.md rule 6).

    python -m oneguard.viseca.demo --scenario <scenario id> [--card <card id>] [--api <url>] [--force]
    python -m oneguard.viseca.demo --offline --scenario <scenario id> [--card <card id>] [--speed-ms 4000]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import socket
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from oneguard.engine.types import Rule
from oneguard.passport.signer import DeviceKey
from oneguard.viseca.client import RUNS_DISABLED_MESSAGE, runs_allowed
from oneguard.viseca.worker import store_run_id

log = logging.getLogger(__name__)

API_ENV = "ONEGUARD_API_URL"
DEVICE_KEY_ENV = "ONEGUARD_DEVICE_KEY"
DEFAULT_API = "https://oneguard.fly.dev"
API_TIMEOUT_S = 30.0
"""C1 compiles within 10 s and D3 waits up to 15 s on the platform; a slow network on top."""
HEALTH_TIMEOUT_S = 10.0
OFFLINE_SPEED_MS = 4000


def no_server(api_base: str) -> str:
    return (
        f"No OneGuard server answers at {api_base}/healthz, so nothing was started (no run, no "
        f"local worker). Check the server is up, or point {API_ENV} at one that is:\n"
        f"  export {API_ENV}=<server url>   # default {DEFAULT_API}"
    )



def rule_to_viseca(rule: Rule) -> dict[str, Any]:
    """A typed rule in Viseca's ``hard_rules`` format: unused optional fields omitted."""
    out: dict[str, Any] = {"field": rule.field, "operator": rule.operator, "value": rule.value}
    for key in ("currency", "scope", "period_days"):
        if getattr(rule, key) is not None:
            out[key] = getattr(rule, key)
    return out


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


async def _call(
    http: httpx.AsyncClient, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> Any:
    reply = await http.request(method, path, json=body, headers=headers)
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
        reply = await http.get("/healthz", timeout=HEALTH_TIMEOUT_S)
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


def device_key_path(api_base: str) -> Path:
    """Where this terminal keeps its device key for ``api_base`` (``ONEGUARD_DEVICE_KEY`` overrides)."""
    override = os.environ.get(DEVICE_KEY_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    host = re.sub(r"[^A-Za-z0-9.-]+", "_", urlsplit(api_base).netloc or "local")
    return Path.home() / ".config" / "oneguard" / f"device-{host}.pem"


async def _enrolled_on(http: httpx.AsyncClient, device: DeviceKey, card: str, out: Callable[[str], None]) -> str | None:
    """This terminal's device id on ``card``, enrolled when the card has no device yet; None
    (and why) while it waits for approval from the card's controller."""
    label = f"demo-live on {socket.gethostname()}"
    enrolled = await _call(http, "POST", f"/api/cards/{card}/devices", {"public_key_jwk": device.jwk, "label": label})
    if enrolled["status"] == "enrolled":
        return str(enrolled["device_id"])
    out(
        f'This terminal ("{label}") is not approved for card {card} yet. Approve it from the card\'s '
        f"controller (its Passport section, Approve), then run this again. Nothing was changed."
    )
    return None


async def run_via_api(
    http: httpx.AsyncClient,
    scenario_id: str,
    *,
    card_id: str | None = None,
    force: bool = False,
    out: Callable[[str], None] = print,
    max_seconds: float = 900.0,
    poll_s: float = 1.0,
    device: DeviceKey | None = None,
) -> int:
    """Start one scenario through the server's API and follow it read-only.

    Confirming the policy (C2) is a device-bound write (api-contract §3.10): this terminal
    signs it with ``device`` (default: its key for this server, ``device_key_path``),
    enrolled on the card as its first device, or approved by one that controls the card.

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
        device = device or DeviceKey.load_or_create(device_key_path(base))
        device_id = await _enrolled_on(http, device, card, out)
        if device_id is None:
            return 1
        instruction = scenario["cardholder_instruction"]
        out(f"Instruction: {instruction}")
        draft = await _call(http, "POST", f"/api/cards/{card}/policy-drafts", {"instruction": instruction})
        out(f"Compiled ({draft.get('compiler', 'unknown')}), uncertainty: {draft['uncertainty_policy']}")
        for check in draft["checks"]:
            out(f"  check {check['id']}: {check['text']} [{check['source']}]")
        for question in draft["open_questions"]:
            out(f"  open question: {question}")
        confirm_path = f"/api/policy-drafts/{draft['draft_id']}/confirm"
        confirm = {
            "checks": draft["checks"],
            "uncertainty_policy": draft["uncertainty_policy"],
            "open_questions": draft["open_questions"],
        }
        signed = device.headers(device_id, "POST", urlsplit(base).path.rstrip("/") + confirm_path, confirm)
        mandate = await _call(http, "POST", confirm_path, confirm, headers=signed)
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
    step-up answered or expired). Only this run's decisions (C6 ``run_id``) are printed, so
    the customer's other runs are skipped, and one the server decided before the first read
    is not.
    """
    ours = {run_id, store_run_id(run_id)}  # offline runs keep D3's id, live runs the ledger's
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
            if d.get("run_id") not in ours or seen.get(d["authorization_id"]) == key:
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


# Entry point -----------------------------------------------------------------------------


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
    parser.add_argument("--offline", action="store_true", help="restart the server's offline replay (D2) instead")
    parser.add_argument("--speed-ms", type=int, default=OFFLINE_SPEED_MS, help="--offline: time between purchases")
    parser.add_argument("--max-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    api_base = args.api.rstrip("/")

    if args.offline:
        return asyncio.run(offline(args.scenario, api_base=api_base, card_id=args.card, speed_ms=args.speed_ms))
    if not runs_allowed():
        print(RUNS_DISABLED_MESSAGE, file=sys.stderr)
        return 2
    return asyncio.run(
        live(args.scenario, api_base=api_base, card_id=args.card, force=args.force, max_seconds=args.max_seconds)
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
    device: DeviceKey | None = None,
) -> int:
    """The server at ``api_base`` decides; with none answering, nothing starts (exit 1)."""
    async with httpx.AsyncClient(base_url=api_base, timeout=API_TIMEOUT_S, transport=transport) as http:
        health = await server_health(http)
        if health is None:
            out(no_server(api_base))
            return 1
        if not health["worker"].get("configured"):
            out(
                f"OneGuard at {api_base} is not connected to the payment platform (no worker), "
                "so it cannot run a scenario; nothing was started."
            )
            return 1
        out(f"OneGuard at {api_base} decides this run; this terminal only starts and follows it.")
        return await run_via_api(
            http, scenario_id, card_id=card_id, force=force, out=out, max_seconds=max_seconds, device=device
        )


async def offline(
    scenario_id: str,
    *,
    api_base: str,
    card_id: str | None = None,
    speed_ms: int = OFFLINE_SPEED_MS,
    out: Callable[[str], None] = print,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """D2 on the server at ``api_base``: its offline replay of the scenario, ``speed_ms`` apart.
    Exit 1 when no server answers (nothing started) or it refuses."""
    async with httpx.AsyncClient(base_url=api_base, timeout=API_TIMEOUT_S, transport=transport) as http:
        if await server_health(http) is None:
            out(no_server(api_base))
            return 1
        try:
            if not card_id:
                listed = (await _call(http, "GET", "/api/scenarios"))["scenarios"]
                card_id = next((s["card_id"] for s in listed if s["scenario_id"] == scenario_id), None)
                if not card_id:
                    out(f"Nobody knows yet which card {scenario_id} runs on; pass --card <card id>.")
                    return 1
            body = {"scenario_id": scenario_id, "card_id": card_id, "speed_ms": speed_ms}
            status = await _call(http, "POST", "/api/dev/replay/restart", body)
        except ApiRefused as exc:
            out(f"{api_base} refused: {exc.message} ({exc.code})")
            return 1
        except httpx.HTTPError as exc:
            out(f"{api_base} did not answer: {type(exc).__name__}")
            return 1
    total = status["total"]
    out(
        f"Replay of {status['scenario_id']} on card {status['card_id']} started at {api_base}: "
        f"{total} purchase{'' if total == 1 else 's'}, {speed_ms} ms apart."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
