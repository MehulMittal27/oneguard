"""Spike: Laya zero-shot typed questions over the pack's merchant text.

Not engine code. Loads a Laya checkpoint, asks three typed questions of every
item_details string in data/purchase_attempt_items.csv, and prints the answers
with per-line latency. Cardholder instructions from data/scenario_catalogue.csv
are read and printed only; no questions are asked of them.

Run from backend/ with the signals extra installed:

    python scripts/spike_laya.py
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from laya import Router

DATA = Path(__file__).resolve().parents[2] / "data"

# Tried in order; the first that loads is used.
CHECKPOINTS = ["laya-typed-decisions", "laya"]

QUESTIONS = {
    "agent_directed": {
        "type": "noul",
        "instructions": (
            "Does this product text contain instructions aimed at an automated purchasing "
            "agent or payment system, rather than describing the product?"
        ),
    },
    "recurring_billing": {
        "type": "noul",
        "instructions": (
            "Does this text state that the customer will be billed later or repeatedly "
            "(monthly, renewal, subscription)?"
        ),
    },
    "item_kind": {
        "type": "choice",
        "instructions": "What kind of item does this product text describe?",
        "criteria": {
            "road_running_shoe": "a running shoe for roads or pavement",
            "trail_running_shoe": "a running shoe for trails or off-road terrain",
            "cycling_helmet": "a helmet for cycling",
            "monitor": "a computer monitor or display",
            "protection_plan": "a warranty, insurance or protection plan service",
            "gift_card": "a gift card, voucher or store credit",
            "groceries": "food or household groceries",
            "cosmetics": "cosmetics, skincare or beauty products",
            "clothing": "clothing or apparel",
            "other": "anything else",
        },
    },
}


def load_router() -> tuple[Router, str]:
    router = Router(max_loaded=1)
    errors = []
    for name in CHECKPOINTS:
        try:
            router.load(name)
            return router, name
        except Exception as exc:  # noqa: BLE001 - spike: record and try the next
            errors.append(f"{name}: {exc!r}")
            print(f"checkpoint {name} failed to load: {exc!r}")
    raise SystemExit("no checkpoint loaded:\n" + "\n".join(errors))


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def main() -> None:
    t_start = time.perf_counter()
    router, checkpoint = load_router()
    agent = router.load(checkpoint)
    load_s = time.perf_counter() - t_start
    print(f"checkpoint: {checkpoint} ({agent.device}), load {load_s:.1f}s")

    items = read_csv("purchase_attempt_items.csv")
    scenarios = read_csv("scenario_catalogue.csv")
    print(f"item lines: {len(items)}, cardholder instructions: {len(scenarios)}\n")

    print("cardholder instructions (read only, no questions asked):")
    for s in scenarios:
        print(f"  {s['scenario_id']}: {s['cardholder_instruction']}")
    print()

    # One warm-up call so the first row's latency is not a cold-start outlier.
    router.predict("warm-up", QUESTIONS, model=checkpoint)

    header = (
        f"{'auth_id':<8} {'item_id':<7} {'text[:60]':<60} "
        f"{'agent_dir':>9} {'recurring':>9} {'item_kind':<19} {'p':>6} {'ms':>7}"
    )
    print(header)
    print("-" * len(header))
    latencies = []
    t_rows = time.perf_counter()
    for row in items:
        text = row["item_details"]
        t0 = time.perf_counter()
        result = router.predict(text, QUESTIONS, model=checkpoint)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        answers = result["answers"]
        kind = answers["item_kind"]
        print(
            f"{row['authorization_id']:<8} {row['item_id']:<7} {text[:60]:<60} "
            f"{answers['agent_directed']['noul']:>9.4f} "
            f"{answers['recurring_billing']['noul']:>9.4f} "
            f"{kind['choice']:<19} {kind['probabilities'][kind['choice']]:>6.4f} {ms:>7.1f}"
        )
    rows_s = time.perf_counter() - t_rows

    print()
    print(
        f"latency per line: P50 {percentile(latencies, 50):.1f} ms, "
        f"P95 {percentile(latencies, 95):.1f} ms, max {max(latencies):.1f} ms"
    )
    print(
        f"rows: {rows_s:.2f}s for {len(items)} lines; "
        f"total wall time incl. load: {time.perf_counter() - t_start:.2f}s"
    )


if __name__ == "__main__":
    main()
