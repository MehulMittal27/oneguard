"""Wall-time probe for the store against ``ONEGUARD_DATABASE_URL`` (docs/database.md §5).

    python scripts/store_latency.py [--runs 7]

Measures, each as the first (cold) run plus the median of ``--runs``:

- ``history load``: a fresh engine (connect + TLS) and ``StoreHistoryIndex.load`` of
  every history, merchant and item row: what the service pays once at startup.
- ``known_merchants``: ``StoreHistoryIndex.known_merchants("CU0019")`` on the loaded
  index (in memory), and the same answer as one SQL query for comparison.
- ``decision write + read``: one ``decisions`` row written from a ``LedgerEntry`` in its
  own committed session, then the run's rows read back and rebuilt as ``LedgerEntry`` in
  a second session: the store round trips a session-backed ``Ledger.record`` + ``view``
  pays. Rows use a fresh ``latency-probe-*`` run id and are deleted afterwards.

Reference data must be seeded (``make seed``). Never prints the database URL.
"""

from __future__ import annotations

import argparse
import statistics
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from oneguard.engine.ledger_base import LedgerEntry
from oneguard.store.db import database_url, make_engine, session
from oneguard.store.history import StoreHistoryIndex
from oneguard.store.schema import AuthorizationHistory, Decision

CUSTOMER = "CU0019"


def timed(fn: Callable[[], Any], runs: int) -> tuple[float, float]:
    """(first run, median of the next ``runs``) in milliseconds."""
    samples = []
    for _ in range(runs + 1):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples[0], statistics.median(samples[1:])


def entry(run_id: str, n: int) -> LedgerEntry:
    at = datetime(2026, 9, 24, 12, 0, tzinfo=UTC) + timedelta(minutes=n)
    return LedgerEntry(
        live_authorization_id=f"{run_id}-{n}", run_id=run_id, mandate_id="mnd_probe",
        card_id="CA0039", customer_id=CUSTOMER, ts_sim=at, outcome="approve", final=True,
        uncertain_outcome=None, merchant_id="ME0023", item_ids=["IT0001"],
        billing_amount_chf=44.5, step=7, deciding_ids=["c1"], reason_codes=["within_mandate"],
        evidence=[], message="probe", engine_version="probe", latency_ms=1.0,
        signals_enabled=False, decided_at=at,
    )  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args()
    print(f"backend: {database_url().split(':', 1)[0]}, runs: 1 cold + median of {args.runs}")

    def load_fresh() -> StoreHistoryIndex:
        engine = make_engine()
        try:
            with session(engine) as s:
                return StoreHistoryIndex.load(s)
        finally:
            engine.dispose()

    cold, median = timed(load_fresh, args.runs)
    print(f"history load (fresh engine) first {cold:8.1f} ms  median {median:8.1f} ms")

    engine = make_engine()
    with session(engine) as s:
        history = StoreHistoryIndex.load(s)
    cold, median = timed(lambda: history.known_merchants(CUSTOMER), args.runs)
    print(f"known_merchants in memory   first {cold:8.4f} ms  median {median:8.4f} ms")

    def known_merchants_sql() -> None:
        with session(engine) as s:
            s.execute(
                select(AuthorizationHistory.merchant_id, func.count())
                .where(
                    AuthorizationHistory.customer_id == CUSTOMER,
                    AuthorizationHistory.transaction_type == "purchase",
                    AuthorizationHistory.status == "approved",
                )
                .group_by(AuthorizationHistory.merchant_id)
            ).all()

    cold, median = timed(known_merchants_sql, args.runs)
    print(f"known_merchants as SQL      first {cold:8.1f} ms  median {median:8.1f} ms")

    run_id = f"latency-probe-{uuid.uuid4().hex[:12]}"
    counter = iter(range(10_000))

    def write_then_read() -> None:
        stored = entry(run_id, next(counter))
        with session(engine) as s:
            s.add(Decision(**stored.model_dump()))
        with session(engine) as s:
            rows = s.scalars(
                select(Decision).where(Decision.run_id == run_id, Decision.ts_sim <= stored.ts_sim)
            ).all()
            [LedgerEntry.model_validate(r, from_attributes=True) for r in rows]

    try:
        cold, median = timed(write_then_read, args.runs)
        print(f"decision write + read       first {cold:8.1f} ms  median {median:8.1f} ms")
    finally:
        with session(engine) as s:
            s.execute(delete(Decision).where(Decision.run_id == run_id))
        engine.dispose()


if __name__ == "__main__":
    main()
