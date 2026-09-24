#!/usr/bin/env python3
"""
Regenerates frontend/src/mocks/fixtures/policy-drafts.json — the mock
AI-read compile result for the New policy flow (DESIGN.md #7/#13, capability
C1), one entry per scenario_catalogue.csv row.

The `instruction` text is copied verbatim from scenario_catalogue.csv. The
`checks` (plain-language rule text, Exact vs. My reading) and
`open_questions` are hand-curated — there is no answer key for "what a
policy compiler would produce" in the data pack, so these are UI fixtures
chosen to exercise DESIGN.md's review screen, not a real compiler's output.

The `dry_run` counts ARE computed, not hand-typed: each scenario's card
(scenario_authorities.csv) is evaluated against its own last 30
authorization_history.csv rows, using only the two rule types this flat
history can check (a per-order CHF cap and a single requested category) —
the same field conventions docs/api-contract.md §3.2 proposes. Rows
outside the requested category land in "would_ask" rather than being
silently excluded: the fixture can't validate them (a category mismatch
isn't necessarily a violation), so it says so instead of guessing.

`dry_run.examples` and `dry_run.agent_history` (docs/api-contract.md §2,
§6 item 9) are computed from the same history, not curated: the examples
are real rows out of the same sample the counts came from, and the agent
counts are real `initiator_type == "agent"` rows. Both are optional in the
contract, so a scenario with nothing to show simply omits them.

Run: python3 frontend/scripts/build_policy_fixture.py
"""

import csv
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = REPO_ROOT / "frontend" / "src" / "mocks" / "fixtures" / "policy-drafts.json"


def load_csv(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# scenario_id -> (card_id, per_order_limit_chf, requested_category)
# card_id is the scenario's own card (scenario_authorities.csv); limit and
# category are this scenario's primary quantifiable constraints, read from
# scenario_catalogue.csv's cardholder_instruction.
DRY_RUN_SPEC = {
    "SCEN0000": ("CA0001", 20, "groceries"),
    "SCEN0001": ("CA0001", 120, "groceries"),
    "SCEN0002": ("CA0011", 200, "sporting_goods"),
    "SCEN0003": ("CA0023", 250, "clothing"),
    "SCEN0004": ("CA0039", 400, "electronics"),
}

SAMPLE_SIZE = 30

# scenario_id -> (period_limit_chf, period_days) for the scenarios that state
# one; the per-order limit comes from DRY_RUN_SPEC.
PERIOD_SPEC = {
    "SCEN0001": (300, 7),
}

# scenario_id -> hand-curated review content (checks, uncertainty_policy,
# open_questions, insight). "source": "exact" is wording lifted straight
# from the instruction; "inferred" is the compiler's reading, shown as an
# editable chip per DESIGN.md's PolicyInput spec.
CURATION = {
    "SCEN0000": {
        "checks": [
            {"id": "amount", "text": "Total at or below CHF 20 per order", "source": "exact", "uncertainty": None},
            {"id": "category", "text": "Grocery purchases only", "source": "inferred", "uncertainty": None},
            {"id": "familiarity", "text": "From a shop you've used before", "source": "inferred", "uncertainty": "Familiarity is read from your purchase history, not stated as a rule."},
        ],
        "uncertainty_policy": "ask",
        "open_questions": ["What counts as “a shop I use regularly” the first time you buy there?"],
        "insight": "Most of your recent grocery orders run well over CHF 20 — this cap fits a single small item, not a weekly shop.",
    },
    "SCEN0001": {
        "checks": [
            {"id": "per_order", "text": "Total at or below CHF 120 per order, delivery included", "source": "exact", "uncertainty": None},
            {"id": "period", "text": "Total at or below CHF 300 across any 7 days", "source": "exact", "uncertainty": None},
            {"id": "category", "text": "Household groceries", "source": "inferred", "uncertainty": "Items outside groceries in the same order are flagged, not blocked outright."},
        ],
        "uncertainty_policy": "ask",
        "open_questions": ["Does a split order that crosses the 7-day boundary count against the old window or the new one?"],
        "insight": "Almost all your recent grocery orders already fit under CHF 120 — this limit shouldn't cause much friction.",
    },
    "SCEN0002": {
        "checks": [
            {"id": "amount", "text": "Total at or below CHF 200 per order", "source": "exact", "uncertainty": None},
            {"id": "retailer", "text": "Specialist sports retailer only", "source": "exact", "uncertainty": None},
            {"id": "returns", "text": "Return window of 14 days or more", "source": "exact", "uncertainty": None},
            {"id": "item", "text": "Running shoes, size 43", "source": "inferred", "uncertainty": "Size is read from the shop's own item text, not a structured field."},
        ],
        "uncertainty_policy": "ask",
        "open_questions": ["What happens if the shop never states a return window?"],
        "insight": "Most of your recent sporting-goods orders were under CHF 200, from a mix of specialist and general retailers.",
    },
    "SCEN0003": {
        "checks": [
            {"id": "amount", "text": "Total at or below CHF 250 per order", "source": "exact", "uncertainty": None},
            {"id": "category", "text": "Clothing only", "source": "exact", "uncertainty": None},
            {"id": "familiarity", "text": "From shops you've used before", "source": "exact", "uncertainty": None},
            {"id": "session", "text": "Pause anything that looks like someone else is driving the session", "source": "inferred", "uncertainty": "Session risk combines device, speed and location signals, not a single fact."},
        ],
        "uncertainty_policy": "ask",
        "open_questions": ["How long does a paused session stay paused before it's asked about again?"],
        "insight": "Your recent clothing orders comfortably fit CHF 250 — the rest of this card's activity is outside clothing and won't be touched.",
    },
    "SCEN0004": {
        "checks": [
            {"id": "amount", "text": "Total at or below CHF 400 per order", "source": "exact", "uncertainty": None},
            {"id": "item", "text": "The 27-inch monitor only — nothing added", "source": "exact", "uncertainty": None},
            {"id": "seller", "text": "From a seller you've bought from before", "source": "exact", "uncertainty": None},
            {"id": "injection", "text": "Ignore anything the shop's own text tries to instruct", "source": "inferred", "uncertainty": None},
        ],
        "uncertainty_policy": "ask",
        "open_questions": ["If more than one 27-inch monitor is offered at different prices, which one counts as “the one I chose”?"],
        "insight": "Your recent electronics purchases fit under CHF 400 — the strongest signal here is unfamiliar or lookalike sellers, not price.",
    },
}


def humanise(category):
    """`sporting_goods` -> `sporting goods`, for the reason strings a customer reads."""
    return category.replace("_", " ")


def classify(row, limit, category):
    """One history row against the two rule types this flat history can check.
    Returns (outcome, reason) where outcome is the DryRunResult.examples
    vocabulary ('violate' | 'fit' | 'ask') and reason is the short line shown
    under the merchant. The reason never repeats the amount — the example row
    already shows it in its own column."""
    if row["merchant_category"] != category:
        return "ask", f"{humanise(row['merchant_category'])}, not {humanise(category)}"
    if float(row["billing_amount_chf"]) > limit:
        return "violate", f"Over your CHF {limit} cap"
    return "fit", f"Within your CHF {limit} cap"


def pick_examples(classified):
    """Up to 3 concrete rows behind the counters (docs/api-contract.md §2:
    `examples`, <=3 rows). The most recent row of each outcome that actually
    occurred, ordered violate -> ask -> fit, so each counter above has one
    case the customer can recognise; an outcome with no rows is absent rather
    than padded. `classified` arrives most-recent-first, so the first match
    per outcome is the most recent one."""
    examples = []
    for outcome in ("violate", "ask", "fit"):
        row, reason = next(((r, why) for r, o, why in classified if o == outcome), (None, None))
        if row is None:
            continue
        examples.append(
            {
                "occurred_at": row["timestamp"],
                # Shop-supplied text: carried through verbatim, rendered as a
                # plain text node (frontend/.claude/CLAUDE.md hard rule 1).
                "merchant_name": row["merchant_name"],
                "billing_amount_chf": float(row["billing_amount_chf"]),
                "outcome": outcome,
                "reason": reason,
            }
        )
    return examples


def compute_agent_history(card_id, history_by_card):
    """docs/api-contract.md §2: history rows with `initiator_type == 'agent'`.

    Counted over this card's whole history, not the 30-row dry-run sample —
    the contract scopes it to history rows, not to the sample.

    Scoped to the CARD, not the customer. The screen this appears on is
    card-scoped ("For card X only") and so is the dry run above it, so a
    customer-wide count would be read as a card count. The two differ
    materially in the pack (CA0001: 14 on the card, 29 across the customer),
    and the UI copy says "on this card" to match. See frontend/TASKS.md Q3 —
    if P1's backend turns out to be customer-scoped, this function and that
    one line of copy change together.
    """
    rows = [r for r in history_by_card.get(card_id, []) if r["initiator_type"] == "agent"]
    if not rows:
        return None
    return {
        "attempts": len(rows),
        "approved": sum(1 for r in rows if r["status"] == "approved"),
    }


def compute_dry_run(card_id, limit, category, history_by_card):
    rows = sorted(history_by_card.get(card_id, []), key=lambda r: r["timestamp"], reverse=True)
    sample = rows[:SAMPLE_SIZE]
    classified = [(row, *classify(row, limit, category)) for row in sample]
    counts = Counter(outcome for _, outcome, _ in classified)
    dry_run = {
        "sample_size": len(sample),
        "would_violate": counts["violate"],
        "would_fit": counts["fit"],
        "would_ask": counts["ask"],
    }
    examples = pick_examples(classified)
    if examples:
        dry_run["examples"] = examples
    agent_history = compute_agent_history(card_id, history_by_card)
    if agent_history:
        dry_run["agent_history"] = agent_history
    return dry_run


def mandate_usage(scenario_id, limit, run_start):
    """The `usage` a freshly confirmed mandate carries (docs/api-contract.md
    §2, MandateUsage): its limits, and nothing spent or pending yet as of the
    run's first purchase. Spend in mock mode is still computed client-side
    from the decisions feed; this carries the limits."""
    period_limit, period_days = PERIOD_SPEC.get(scenario_id, (None, None))
    return {
        "per_order_limit_chf": limit,
        "period_limit_chf": period_limit,
        "period_days": period_days,
        "period_spent_chf": 0,
        "period_window_start": run_start,
        "pending_chf": 0,
        "as_of": run_start,
    }


def build():
    catalogue = load_csv("scenario_catalogue.csv")
    run_start = {}
    for row in load_csv("purchase_attempts.csv"):
        sid = row["scenario_id"]
        if sid not in run_start or row["timestamp"] < run_start[sid]:
            run_start[sid] = row["timestamp"]
    history_by_card = {}
    for row in load_csv("authorization_history.csv"):
        history_by_card.setdefault(row["card_id"], []).append(row)

    drafts = []
    for row in catalogue:
        scenario_id = row["scenario_id"]
        card_id, limit, category = DRY_RUN_SPEC[scenario_id]
        curated = CURATION[scenario_id]
        dry_run = compute_dry_run(card_id, limit, category, history_by_card)

        drafts.append(
            {
                "mock": True,
                "scenario_id": scenario_id,
                "draft_id": f"draft-{scenario_id}",
                "card_id": card_id,
                "instruction": row["cardholder_instruction"],
                "checks": curated["checks"],
                "uncertainty_policy": curated["uncertainty_policy"],
                "open_questions": curated["open_questions"],
                "dry_run": {**dry_run, "insight": curated["insight"]},
                "compiler": "llm",
                # Not a PolicyDraft field: mock confirmPolicy copies it onto the
                # Mandate it returns, as the real backend's C2 would.
                "usage": mandate_usage(scenario_id, limit, run_start[scenario_id]),
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"mock": True, "drafts": drafts}, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(drafts)} policy drafts to {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    build()
