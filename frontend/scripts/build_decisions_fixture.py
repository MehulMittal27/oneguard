#!/usr/bin/env python3
"""
Regenerates frontend/src/mocks/fixtures/decisions.json — the mock decision
feed for the Activity screen (DESIGN.md #3, capability C6).

All merchant names, amounts, item text and timestamps below are pulled by
ID join from data/ (never retyped). The only hand-authored part
is CURATION: which real authorization_id gets which mock decision, reason
codes, message and evidence — there is no answer key in the data pack
(metadata.json: contains_expected_decisions: false), so these are UI
fixtures chosen to exercise states, not a decision engine's output.

Run: python3 frontend/scripts/build_decisions_fixture.py
"""

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = REPO_ROOT / "frontend" / "src" / "mocks" / "fixtures" / "decisions.json"


def load_csv(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def money(value):
    return round(float(value), 2)


# authorization_id -> curated {decision, reason_codes, message, uncertainty, injection_flag, evidence}
# plus, on some rows, the optional contract additions in OPTIONAL_KEYS.
CURATION = {
    "AU0001": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 20.00, within your CHF 120 per-order and CHF 300 seven-day limits.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 20.00 is within your CHF 120 per-order limit."},
            {"rule": "Seven-day limit", "outcome": "pass", "detail": "Within your CHF 300 rolling seven-day limit."},
        ],
    },
    "AU0002": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 44.50, within your CHF 120 per-order and CHF 300 seven-day limits.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 44.50 is within your CHF 120 per-order limit."},
            {"rule": "Seven-day limit", "outcome": "pass", "detail": "Within your CHF 300 rolling seven-day limit."},
        ],
    },
    "AU0003": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 120.00, right at your CHF 120 per-order limit, within your CHF 300 seven-day limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 120.00 is exactly your CHF 120 per-order limit."},
            {"rule": "Seven-day limit", "outcome": "pass", "detail": "Within your CHF 300 rolling seven-day limit."},
        ],
    },
    "AU0005": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 70.00 — your rolling seven-day total is now close to your CHF 300 limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 70.00 is within your CHF 120 per-order limit."},
            {"rule": "Seven-day limit", "outcome": "pass", "detail": "CHF 254.50 of your CHF 300 rolling seven-day limit, after this order."},
        ],
    },
    "AU0010": {
        "decision": "stopped",
        "status": "final",
        "reason_codes": ["per_order_limit_exceeded"],
        "message": "CHF 18.00 over your CHF 120 per-order limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "fail", "detail": "CHF 138.00 is CHF 18.00 over your CHF 120 per-order limit."},
        ],
    },
    "AU0006": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["unrequested_item"],
        "message": "Not sure the fragrance and beauty gift set is part of your household groceries.",
        "uncertainty": {"note": "Whether a cosmetics gift set counts as a household grocery item."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 62.00 is within your CHF 120 per-order limit."},
            {"rule": "Item matches request", "outcome": "uncertain", "detail": "A boxed fragrance and beauty gift set isn't clearly a household grocery."},
        ],
    },
    "AU0012": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 165.00, from a specialist retailer, returnable within 30 days.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 165.00 is within your CHF 200 per-order limit."},
            {"rule": "Return window", "outcome": "pass", "detail": "Returnable within 30 days, at least your required 14."},
        ],
    },
    "AU0021": {
        "decision": "stopped",
        "status": "final",
        "reason_codes": ["per_order_limit_exceeded"],
        "message": "CHF 15.00 over your CHF 200 per-order limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "fail", "detail": "CHF 215.00 is CHF 15.00 over your CHF 200 per-order limit."},
        ],
    },
    "AU0016": {
        "decision": "uncertain",
        "uncertain_outcome": "expired",
        "status": "final",
        "reason_codes": ["return_terms_unknown"],
        "message": "You didn't answer in time. The seller never stated a return policy.",
        "uncertainty": {"note": "Whether the order can be returned within your required 14 days — the seller didn't say."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 175.00 is within your CHF 200 per-order limit."},
            {"rule": "Return window", "outcome": "uncertain", "detail": "The seller's listing doesn't state a return policy."},
        ],
    },
    "AU0018": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["unrequested_item"],
        "message": "Not sure about the extended protection plan — you asked for shoes only.",
        "uncertainty": {"note": "Whether the CHF 29 extended protection plan add-on was wanted."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 194.00 is within your CHF 200 per-order limit."},
            {"rule": "Item matches request", "outcome": "uncertain", "detail": "An extended protection plan wasn't part of “replace my worn shoes”."},
        ],
    },
    # A restriction no data can check: nothing in the event says whether GreenLoop
    # is "a specialist sports retailer", and no field can (engine/policy.py
    # is_unverifiable). CHF 189 is inside the CHF 200 cap, so the amount is not
    # the question — the shop is. This is the one case where approving can also
    # be remembered, which is what `confirmable` tells the UI.
    "AU0022": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["rule_not_met", "unfamiliar_merchant"],
        "message": "CHF 189.00, inside your CHF 200 limit — but nothing says whether this shop counts as a specialist sports retailer.",
        "uncertainty": {"note": "Your rule asks for a specialist sports retailer. Nothing in this order says whether GreenLoop is one, and no record can settle it."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 189.00 is within your CHF 200 limit."},
            {"rule": "Retailer", "outcome": "uncertain", "detail": "No record says whether GreenLoop is a specialist sports retailer.", "source": "policy"},
        ],
        "confirmable": {"rule_id": "retailer", "phrase": "a specialist sports retailer"},
    },
    "AU0024": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 145.00, within your CHF 250 per-order limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 145.00 is within your CHF 250 per-order limit."},
        ],
    },
    "AU0030": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 248.00, CHF 2.00 under your CHF 250 per-order limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 248.00 is CHF 2.00 under your CHF 250 per-order limit."},
        ],
    },
    # SCEN0003 is the session-integrity scenario, and AU0027-AU0030 is its burst:
    # four orders on this card between 02:14 and 02:24. This one is the step-up
    # the freeze produces, so a frozen session is reachable in mock mode.
    "AU0029": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["new_device_burst", "unusual_activity"],
        "message": "Third order on this card in seven minutes, at 02:21. Within your limit, but the pace and hour don't look like you.",
        "uncertainty": {"note": "Whether you are the one driving this session, or something else is using your agent."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 219.00 is within your CHF 250 per-order limit."},
            {"rule": "Session", "outcome": "fail", "detail": "Three orders in seven minutes, between 02:14 and 02:21.", "source": "ledger"},
            {"rule": "Time of day", "outcome": "uncertain", "detail": "02:21 Europe/Zurich, outside this card's usual hours.", "source": "history"},
        ],
        "session": {
            "trust": "frozen",
            "note": "We're double-checking after unusual activity on your card",
        },
    },
    # The day after the 02:14-02:24 burst. CHF 95.00 is far inside the CHF 250
    # cap, so this step-up is purely the session watch — the amount is not what
    # is being asked about.
    "AU0031": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["session_watch"],
        "message": "CHF 95.00, well inside your CHF 250 limit. We're checking with you because this card is still under watch after Tuesday's burst.",
        "uncertainty": {"note": "Whether the burst in the early hours of 18 August was you. Until you say, this card's orders come to you first."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 95.00 is well within your CHF 250 limit."},
            {"rule": "Session", "outcome": "uncertain", "detail": "This card is under watch after four orders between 02:14 and 02:24 on 18 August.", "source": "ledger"},
        ],
        "session": {"trust": "elevated", "note": "On watch since the burst on 18 August. Your answer lifts it."},
    },
    # After the customer answers: the watch lifts and the next order goes through
    # on the rules alone. EUR 260.00 converts to CHF 247.00, inside the cap.
    "AU0032": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["session_recovered", "within_limits", "foreign_currency_converted"],
        "message": "CHF 247.00, within your CHF 250 limit — the watch on this card lifted after you answered.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "EUR 260.00 converts to CHF 247.00, within your CHF 250 limit."},
            {"rule": "Session", "outcome": "pass", "detail": "Normal activity resumed after you confirmed the earlier order.", "source": "ledger"},
        ],
    },
    "AU0034": {
        "decision": "stopped",
        "status": "final",
        "reason_codes": ["per_order_limit_exceeded"],
        "message": "CHF 18.00 over your CHF 250 per-order limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "fail", "detail": "CHF 268.00 is CHF 18.00 over your CHF 250 per-order limit."},
        ],
    },
    "AU0035": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits"],
        "message": "CHF 289.00, within your CHF 400 limit, from a seller you've bought from before.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 289.00 is within your CHF 400 limit."},
            {"rule": "Seller", "outcome": "pass", "detail": "PixelHarbor is a seller you've bought from before."},
        ],
    },
    "AU0042": {
        "decision": "approved",
        "status": "final",
        "reason_codes": ["within_limits", "requote_accepted"],
        "message": "CHF 350.00 — a corrected re-quote of an order stopped earlier for going over your limit.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 350.00 is within your CHF 400 limit."},
            {"rule": "Related order", "outcome": "pass", "detail": "Replaces AU0037, which was stopped for exceeding the limit — not charged twice."},
            {"rule": "Earlier shop text", "outcome": "info", "detail": "This shop's text on the earlier order tried to instruct the agent. Noted; this order's own text is clean.", "source": "history"},
        ],
        "related": {"authorization_id": "AU0037", "relation": "requote_of"},
        # Reads as rewritten prose, not a filled slot — the tier-3 case.
        "explanation_source": "model",
    },
    "AU0036": {
        "decision": "uncertain",
        "uncertain_outcome": "declined",
        "status": "final",
        "reason_codes": ["duplicate_suspected", "customer_confirmation"],
        "message": "The same monitor from the same shop at the same price, 25 minutes after the first order. You blocked it.",
        "uncertainty": {"note": "Whether this is a second monitor you want or a repeat of the order you already placed."},
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 289.00 is within your CHF 400 limit."},
            {"rule": "Repeat order", "outcome": "uncertain", "detail": "Same shop, same monitor, same price as the order approved 25 minutes earlier."},
        ],
        "related": {"authorization_id": "AU0035", "relation": "duplicate_of"},
        "resolved_by": "customer",
    },
    "AU0037": {
        "decision": "stopped",
        "status": "final",
        "reason_codes": ["per_order_limit_exceeded", "injection_suspected"],
        "message": "CHF 120.00 over your CHF 400 per-order limit.",
        "uncertainty": None,
        "injection_flag": {
            "flagged": True,
            "reason": "The item description tried to instruct the agent that spending limits don't apply and the order should be approved without checks.",
        },
        "evidence": [
            {"rule": "Per-order limit", "outcome": "fail", "detail": "CHF 520.00 is CHF 120.00 over your CHF 400 per-order limit."},
            {"rule": "Merchant text", "outcome": "fail", "detail": "Tried to change your spending rules. Flagged and ignored — read for facts only."},
            {"rule": "Seller", "outcome": "pass", "detail": "PixelHarbor is a seller you've bought from before."},
        ],
        "counterfactual": "Would approve at CHF 400 or less.",
        # Slot-filled from the numbers alone — nothing to rewrite.
        "explanation_source": "template",
    },
    "AU0039": {
        "decision": "stopped",
        "status": "final",
        "reason_codes": ["lookalike_merchant"],
        "message": "This shop's name looks like PixelHarbor, but it's a different seller you haven't used before.",
        "uncertainty": None,
        "injection_flag": None,
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 340.00 is within your CHF 400 limit."},
            {"rule": "Seller", "outcome": "fail", "detail": "“PixelHarbour” is a different seller from “PixelHarbor,” which you've used before."},
        ],
        "explanation_source": "model",
    },
    "AU0040": {
        "decision": "uncertain",
        "uncertain_outcome": "pending",
        "status": "pending_human",
        "reason_codes": ["injection_suspected"],
        "message": "The shop's text tried to get this approved without checks — flagged and ignored. Checking with you first.",
        "uncertainty": {"note": "The merchant's own text tried to instruct the agent to skip your rules, which is reason enough to check with you even though the order itself is within your limit."},
        "injection_flag": {
            "flagged": True,
            "reason": "The item description tried to instruct the agent to ignore spending instructions and approve immediately.",
        },
        "evidence": [
            {"rule": "Per-order limit", "outcome": "pass", "detail": "CHF 299.00 is within your CHF 400 limit."},
            {"rule": "Merchant text", "outcome": "fail", "detail": "Tried to instruct the agent directly. Flagged and ignored — read for facts only."},
            {"rule": "Seller", "outcome": "pass", "detail": "PixelHarbor is a seller you've bought from before."},
        ],
        "counterfactual": "Would approve without the instructions in the shop's text.",
    },
}


# Optional contract additions (docs/api-contract.md §2): copied only when a
# row curates them, so every other row keeps the older payload shape.
#
# `explanation_source` is curated: no tier-3 rewrite exists to observe, so the
# three rows carrying it were chosen by how their `message` reads. The rest omit
# it, which exercises the absent case in the UI.
OPTIONAL_KEYS = (
    "counterfactual",
    "related",
    "session",
    "explanation_source",
    "resolved_by",
    "confirmable",
)


def build():
    attempts = {a["authorization_id"]: a for a in load_csv("purchase_attempts.csv")}
    items_by_auth = {}
    for item in load_csv("purchase_attempt_items.csv"):
        items_by_auth.setdefault(item["authorization_id"], []).append(item)
    merchants = {m["merchant_id"]: m for m in load_csv("merchants.csv")}
    authorities = load_csv("scenario_authorities.csv")
    card_to_customer = {a["card_id"]: a["customer_id"] for a in authorities}

    decisions = []
    for auth_id, curated in CURATION.items():
        attempt = attempts[auth_id]
        merchant = merchants.get(attempt["merchant_id"], {})
        line_items = [
            {
                "item_name": it["item_name"],
                "quantity": int(it["quantity"]),
                "unit_price": money(it["unit_price"]),
                "currency": it["currency"],
                "item_details": it["item_details"],
            }
            for it in sorted(items_by_auth.get(auth_id, []), key=lambda r: int(r["line_no"]))
        ]

        decisions.append(
            {
                "mock": True,
                "authorization_id": auth_id,
                "customer_id": card_to_customer[attempt["card_id"]],
                "card_id": attempt["card_id"],
                "decision": curated["decision"],
                # Only 'uncertain' rows set this (D-041) — everything else omits
                # the key in CURATION, defaulting to None/null here.
                "uncertain_outcome": curated.get("uncertain_outcome"),
                "status": curated["status"],
                "reason_codes": curated["reason_codes"],
                "message": curated["message"],
                "uncertainty": curated["uncertainty"],
                "occurred_at": attempt["timestamp"],
                "merchant": {
                    "merchant_id": attempt["merchant_id"],
                    "name": merchant.get("merchant_name"),
                },
                "amount": money(attempt["amount"]),
                "currency": attempt["currency"],
                "billing_amount_chf": money(attempt["billing_amount_chf"]),
                "items": line_items,
                "injection_flag": curated["injection_flag"],
                "evidence": curated["evidence"],
                "order_returnable": attempt["order_returnable"],
                "delivery_by": attempt["delivery_by"] or None,
                **{key: curated[key] for key in OPTIONAL_KEYS if key in curated},
            }
        )

    decisions.sort(key=lambda d: d["occurred_at"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"mock": True, "decisions": decisions}, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(decisions)} decisions to {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    build()
