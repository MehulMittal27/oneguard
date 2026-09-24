"""``make demo-live SCEN=<scenario id>``: one scenario end to end against the Viseca sandbox.

1. starts the worker (bootstrap, reference-data check, long-poll loop);
2. compiles the scenario's instruction through the compile path (``compile_instruction``:
   P4's compiler when it has landed, the stub / fallback before that);
3. creates the mandate at Viseca with the instruction verbatim and the typed rules as
   ``hard_rules``, confirms it and binds the policy to the worker;
4. starts the run and prints every decision and the run's progress until it is done and
   no step-up is still waiting (unanswered ones expire after the human window).

Nothing here answers a step-up: there is no customer in a terminal demo (CLAUDE.md
rule 6). Without ``VISECA_API_KEY`` it exits with a message and status 2.

    python -m oneguard.viseca.demo --scenario <scenario id> [--card <card id>]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, select

from oneguard.api import models as api
from oneguard.engine import stubs
from oneguard.engine.types import CompiledDraft, Policy, Rule
from oneguard.llm.provider import Provider, get_provider
from oneguard.store import seed as seed_module
from oneguard.store.db import get_engine, init_db, session
from oneguard.store.schema import ScenarioCatalogue
from oneguard.viseca.client import API_KEY_ENV, VisecaClient, store_sink
from oneguard.viseca.worker import (
    POLL_WAIT_S,
    VisecaWorker,
    first_value,
    run_total,
    walk_json,
)

log = logging.getLogger(__name__)

MISSING_KEY = (
    f"{API_KEY_ENV} is not set. The live demo talks to the Viseca sandbox and needs your "
    "team's bearer key.\nExport it in this shell (it stays server-side, never in git) and "
    f"rerun:\n  export {API_KEY_ENV}=<team key>\n  make demo-live SCEN=<scenario id>"
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


def _line(decision: api.Decision) -> str:
    state = decision.decision
    if decision.uncertain_outcome and decision.uncertain_outcome != "pending":
        state += f"/{decision.uncertain_outcome}"
    elif decision.status == "pending_human":
        state += f" (waiting until {decision.deadline_at:%H:%M:%S})" if decision.deadline_at else ""
    codes = ",".join(decision.reason_codes)
    return (
        f"  {decision.authorization_id}  {state:<22} CHF {decision.billing_amount_chf:>8.2f}  "
        f"[{codes}] {decision.message}"
    )


async def run_demo(
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
    """Compile, confirm, run and tail one scenario. 0 when the run finished in time."""
    provider = provider or get_provider()
    worker = VisecaWorker(client, db=db, provider=provider, poll_wait_s=poll_wait_s, **worker_options)
    await worker.start()
    try:
        instruction, served_card = scenario_from_reference(worker.reference_data, scenario_id)
        instruction = instruction or await asyncio.to_thread(scenario_from_store, db, scenario_id)
        if not instruction:
            out(f"No instruction found for scenario {scenario_id}.")
            return 1
        card = card_id or served_card or ""
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

        worker.add_listener(lambda decision: out(_line(decision)))
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
    parser.add_argument("--card", help="card id for the dry-run (default: from reference data)")
    parser.add_argument("--max-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not os.environ.get(API_KEY_ENV, "").strip():
        print(MISSING_KEY, file=sys.stderr)
        return 2

    db = get_engine()
    init_db(db)
    with session(db) as s:
        seeded = seed_module.history_row_count(s) > 0
    if not seeded:
        log.info("store is empty; seeding the reference tables first")
        seed_module.run(engine=db)

    async def go() -> int:
        async with VisecaClient(sink=store_sink(db)) as client:
            return await run_demo(
                client, args.scenario, db=db, card_id=args.card, max_seconds=args.max_seconds
            )

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
