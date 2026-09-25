#!/usr/bin/env python3
"""
End-to-end check of the two policy-refresh flows, against a running backend.

The UI has no browser test harness, so this drives the real API with exactly
the requests `PolicyProvider` makes (C10 once, then C3 for every card) and
asserts what the screens are built on:

1. Reload and sign in again: every card's policy comes back from C3, so a card
   with an active policy shows it and is not offered "+ Add policy" (which is
   offered only for a card C3 answers `null` for).
2. After a replayed approval, the refresh that follows the decisions poll
   carries the ledger's spend in `usage.period_spent_chf`, which is what the
   meter reads.

Run it on a fresh store, the way docs/api-contract.md's local run does:

    make seed && make serve            # in another terminal, no keys, SQLite
    python3 scripts/check_policy_refresh.py [http://localhost:8000]

Standard library only. It confirms a policy on CA0001 and replays SCEN0000
there through the operator endpoint (D2), so run it on a throwaway store. Against a
production server, set ONEGUARD_OPERATOR_TOKEN: D2 needs it.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
CUSTOMER = "CU0001"
CARD = "CA0001"
SCENARIO = "SCEN0000"
# What the customer types; the backend compiles it (model or rule-based fallback).
INSTRUCTION = (
    "Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. "
    "Ask me when uncertain. Keep it to CHF 300 across any 7 days."
)


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    # A production server refuses /api/dev/* without the operator token (docs/api-contract.md §1.2).
    if path.startswith("/api/dev/") and (token := os.environ.get("ONEGUARD_OPERATOR_TOKEN", "").strip()):
        headers["X-OneGuard-Operator"] = token
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None


def ok(method: str, path: str, body: dict | None = None) -> dict | None:
    status, payload = call(method, path, body)
    if status not in (200, 204):
        sys.exit(f"FAIL {method} {path}: {status} {payload}")
    return payload


def sign_in() -> dict[str, dict | None]:
    """What the UI reads on sign-in: C12, then PolicyProvider's C10 and C3 per card."""
    customers = ok("GET", "/api/customers")["customers"]
    assert any(c["customer_id"] == CUSTOMER for c in customers), f"{CUSTOMER} not listed"
    accounts = ok("GET", f"/api/customers/{CUSTOMER}/accounts")["accounts"]
    cards = [card["card_id"] for account in accounts for card in account["cards"]]
    return {card: ok("GET", f"/api/cards/{card}/policy")["mandate"] for card in cards}


def main() -> None:
    policies = sign_in()
    if policies.get(CARD) is not None:
        sys.exit(f"FAIL {CARD} already has a policy; run this on a freshly seeded store")
    print(f"sign-in: {len(policies)} cards, {CARD} has no policy, so '+ Add policy' is right")

    draft = ok("POST", f"/api/cards/{CARD}/policy-drafts", {"instruction": INSTRUCTION})
    confirmed = ok(
        "POST",
        f"/api/policy-drafts/{draft['draft_id']}/confirm",
        {
            "checks": draft["checks"],
            "uncertainty_policy": draft["uncertainty_policy"],
            "open_questions": draft["open_questions"],
        },
    )
    print(f"confirmed {confirmed['mandate_id']} on {CARD} (compiler: {draft.get('compiler')})")

    # Flow 1: a reload drops all client state, so this is a sign-in from nothing.
    policies = sign_in()
    mandate = policies[CARD]
    assert mandate is not None, f"FAIL flow 1: C3 returned null for {CARD} after a reload"
    assert mandate["mandate_id"] == confirmed["mandate_id"], "FAIL flow 1: a different mandate came back"
    assert mandate["status"] == "active", f"FAIL flow 1: status {mandate['status']}"
    print(f"flow 1 ok: after a reload C3 returns {mandate['mandate_id']} active; no '+ Add policy'")

    spent_before = mandate["usage"]["period_spent_chf"]
    ok("POST", "/api/dev/replay/restart", {"scenario_id": SCENARIO, "card_id": CARD, "speed_ms": 0})

    # Flow 2: the UI polls C6 and refreshes C3 after each poll; do the same until
    # the replay is delivered and its decision is in the feed.
    deadline = time.monotonic() + 30
    while True:
        replay = ok("GET", "/api/dev/replay")
        decisions = ok("GET", f"/api/customers/{CUSTOMER}/decisions")["decisions"]
        usage = ok("GET", f"/api/cards/{CARD}/policy")["mandate"]["usage"]
        if not replay["running"] and replay["delivered"] == replay["total"] and decisions:
            break
        if time.monotonic() > deadline:
            sys.exit(f"FAIL flow 2: replay not delivered in time: {replay}")
        time.sleep(0.5)

    approved = [
        d
        for d in decisions
        if d["card_id"] == CARD
        and (d["decision"] == "approved" or d["uncertain_outcome"] == "approved")
    ]
    assert approved, "FAIL flow 2: the replay produced no approval"
    expected = round(spent_before + sum(d["billing_amount_chf"] for d in approved), 2)
    assert usage["period_spent_chf"] == expected, (
        f"FAIL flow 2: C3 usage says CHF {usage['period_spent_chf']:.2f}, "
        f"the approvals add up to CHF {expected:.2f}"
    )
    assert usage["period_spent_chf"] > 0, "FAIL flow 2: nothing spent"
    print(
        f"flow 2 ok: after the replayed approval C3 usage reads CHF {usage['period_spent_chf']:.2f} "
        f"of CHF {usage['period_limit_chf']:.2f} (the meter's number)"
    )


if __name__ == "__main__":
    main()
