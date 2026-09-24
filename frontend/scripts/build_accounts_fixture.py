#!/usr/bin/env python3
"""
Regenerates frontend/src/mocks/fixtures/accounts.json — the mock accounts
list for the Accounts screen (DESIGN.md #5, capability C10).

Every field is joined by ID from data/accounts.csv and
cards.csv, never retyped. Every account belonging to each live customer is
included (D-057) — not just the primary/scenario one. Two of the four
(CU0012, CU0019) genuinely have a second account in the data pack
(ROADMAP.md slice 6 originally deferred this, ADR D-037); the other two
(CU0001, CU0006) have one account with two cards on it (D-050). Every card
on every included account is listed, sorted by card_id.

Run: python3 frontend/scripts/build_accounts_fixture.py
"""

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = REPO_ROOT / "frontend" / "src" / "mocks" / "fixtures" / "accounts.json"

# customer_id -> primary card_id, matching frontend/src/mocks/fixtures/customers.json
LIVE_CARDS = {
    "CU0001": "CA0001",
    "CU0006": "CA0011",
    "CU0012": "CA0023",
    "CU0019": "CA0039",
}


def load_csv(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def money(value):
    return round(float(value), 2)


def build():
    accounts = load_csv("accounts.csv")
    cards = load_csv("cards.csv")
    cards_by_account = {}
    for c in cards:
        cards_by_account.setdefault(c["account_id"], []).append(c)

    result = []
    for customer_id in LIVE_CARDS:
        customer_accounts = sorted(
            (a for a in accounts if a["customer_id"] == customer_id),
            key=lambda a: a["account_id"],
        )
        for account in customer_accounts:
            result.append(
                {
                    "mock": True,
                    "account_id": account["account_id"],
                    "customer_id": customer_id,
                    "account_type": account["account_type"],
                    "account_purpose": account["account_purpose"],
                    "status": account["status"],
                    "per_transaction_limit_chf": money(account["per_transaction_limit_chf"]),
                    "monthly_limit_chf": money(account["monthly_limit_chf"]),
                    "cards": [
                        {
                            "card_id": c["card_id"],
                            "card_type": c["card_type"],
                            "card_purpose": c["card_purpose"],
                            "status": c["status"],
                        }
                        for c in sorted(
                            cards_by_account.get(account["account_id"], []),
                            key=lambda c: c["card_id"],
                        )
                    ],
                }
            )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"mock": True, "accounts": result}, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(result)} accounts to {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    build()
