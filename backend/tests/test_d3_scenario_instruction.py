"""D3 ``policy: "scenario"``: the operator console's judging run under the scenario's own
instruction (docs/api-contract.md §1.2, docs/decisions.md).

The platform refuses a run whose mandate's instruction is not the scenario's cardholder
instruction exactly (409 ``instruction_mismatch``, live on 25 Sep 2026). With ``policy:
"scenario"`` D3 drafts the served instruction verbatim (C1's path), confirms every check it
proposes (C2's path, no device signature: an operator run), registers it at the platform,
makes it the card's active policy (the earlier one ``superseded``, with a note), re-issues
the passports and starts the run under it. A refusal of the run carries the platform's
status, code and message verbatim.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from typing import Any

import httpx

from oneguard.engine.facts import ZURICH
from oneguard.passport.ids import passport_id_for
from oneguard.pipeline import _no_active_policy_detail
from oneguard.store import worker_events
from oneguard.store.db import session
from oneguard.store.schema import Mandate
from tests.fake_viseca import INSTRUCTION_MISMATCH, FakeViseca, judging_pack
from tests.test_api_contract import (  # noqa: F401  fixtures
    Running,
    confirm_form,
    db_url,
    fast,
    running,
    seeded_db,
    until,
)

REAL_COMPILER = {"implementations": None, "stubbed": None}
"""D3 compiles the scenario's instruction: the real (rule-based fallback) compiler."""
SCEN0000 = "Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly. Ask me when uncertain."


def stored(run: Running, mandate_id: str) -> Mandate:
    with session(run.services.db_engine) as s:
        row = s.get(Mandate, mandate_id)
        assert row is not None
        return row


async def start(run: Running, scenario_id: str = "SCEN0000", card_id: str = "CA0001", **extra: Any) -> httpx.Response:
    return await run.post("/api/dev/runs", json={"scenario_id": scenario_id, "card_id": card_id, **extra})


def test_a_card_with_another_policy_runs_the_scenarios_instruction_in_its_place(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast(check_instruction=True))
        async with running(db_url, fake=fake, **REAL_COMPILER) as run:
            typed = await confirm_form(run)  # the customer's own policy, in other words
            (typed_tm,) = fake.mandates
            before = (await run.get("/api/cards/CA0001/passport")).json()
            assert before["passport_id"] == passport_id_for(typed["mandate_id"])

            # under the card's policy the platform refuses, and D3 passes its words on
            refused = await start(run)
            assert refused.status_code == 503
            error = refused.json()["error"]
            assert error["code"] == "upstream_unavailable"
            assert error["message"] == "The payment platform did not accept the new run; nothing was changed."
            assert error["detail"] == {
                "platform_status": 409,
                "platform_code": "instruction_mismatch",
                "platform_message": INSTRUCTION_MISMATCH,
            }
            assert not fake.runs and stored(run, typed["mandate_id"]).status == "active"

            # the console's judging run: the scenario's instruction replaces it for the run
            r = await start(run, policy="scenario")
            assert r.status_code == 200, r.text
            live = r.json()
            new_tm = live["platform_mandate"]["viseca_mandate_id"]
            assert live["platform_mandate"] == {
                "status_before": None,
                "reregistered": False,
                "viseca_mandate_id": new_tm,
                "previous_viseca_mandate_id": typed_tm,
                "registered_for_run": True,
                "replaced_mandate_id": typed["mandate_id"],
            }
            (fake_run,) = fake.runs.values()
            assert fake_run.mandate["mandate_id"] == new_tm
            assert fake.mandates[new_tm]["instruction"] == SCEN0000  # verbatim, never retyped
            assert fake.mandates[new_tm]["hard_rules"]  # its checks, as C2 sends them
            assert fake.mandates[typed_tm]["status"] != "active"

            # the card's active policy is the scenario's; the customer's is superseded, noted
            policy = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert policy["mandate_id"] == live["mandate_id"] != typed["mandate_id"]
            assert (policy["instruction"], policy["status"]) == (SCEN0000, "active")
            assert policy["checks"]
            new = stored(run, live["mandate_id"])
            assert new.viseca_mandate_id == new_tm
            assert new.note == "Registered for the operator's judging run of SCEN0000, without the customer's confirmation."
            old = stored(run, typed["mandate_id"])
            assert (old.status, old.viseca_mandate_id) == ("superseded", typed_tm)
            assert old.revoked_at is not None
            assert old.note == f"Replaced by {live['mandate_id']} for the operator's judging run of SCEN0000."
            # a purchase still arriving under the old platform mandate says why it has no policy
            assert run.services.worker is not None
            bound = run.services.worker._policies[typed_tm]
            assert (bound.status, bound.revoked_at) == ("superseded", old.revoked_at.replace(tzinfo=UTC))
            assert _no_active_policy_detail(bound, "CA0001") == (
                f"Policy {typed['mandate_id']} was replaced for an operator's judging run at "
                f"{old.revoked_at.replace(tzinfo=UTC).astimezone(ZURICH):%d %b %Y, %H:%M} Swiss time."
            )

            # passports: the card's is the new policy's, first version says so; the old one
            # gets a new version that ends it
            after = (await run.get("/api/cards/CA0001/passport")).json()
            assert after["passport_id"] == passport_id_for(live["mandate_id"])
            assert after["document"]["instruction"] == SCEN0000
            assert after["document"]["platform_mandate_id"] == new_tm
            assert [v["reason"] for v in after["versions"]] == ["operator_run"]
            assert run.services.book is not None
            found = run.services.book.passport(before["passport_id"])
            assert found is not None
            latest, versions = found
            assert latest.version == before["version"] + 1 and latest.revoked_at is not None
            assert [v.reason for v in versions][-1] == "revoked"

            # the run decides under the scenario's policy, and D4 / D7 keep the evidence
            async def decided() -> list[dict[str, Any]]:
                return [d for d in await run.decisions() if d["run_id"] == live["ledger_run_id"]]

            (decision,) = await until(decided)
            assert "no_active_policy" not in decision["reason_codes"]
            assert (await run.get(f"/api/dev/runs/{live['run_id']}")).json()["platform_mandate"] == live["platform_mandate"]
            assert (await run.get("/api/dev/runs/current")).json()["platform_mandate"] == live["platform_mandate"]

    asyncio.run(scenario())


REFUSED = {
    "platform_status": 409,
    "platform_code": "instruction_mismatch",
    "platform_message": INSTRUCTION_MISMATCH,
}
NOTHING_CHANGED = "The payment platform did not accept the new run; nothing was changed."


def refuse_the_run(fake: FakeViseca) -> None:
    """The platform serves another instruction than the store has (changed since the last
    sync): D3 registers the stored one, and the platform refuses the run."""
    fake.instruction_of = lambda _scenario_id: SCEN0000 + " Thanks."  # type: ignore[method-assign]


def test_a_refused_run_is_undone_and_the_card_keeps_its_policy(db_url: str) -> None:  # noqa: F811
    """D3 registered the scenario's policy and the platform refused the run: the new platform
    mandate is revoked, the card's own policy stays active with its passport, and the
    operator reads the platform's own words and "nothing was changed"."""

    async def scenario() -> None:
        fake = FakeViseca(fast(check_instruction=True))
        async with running(db_url, fake=fake, **REAL_COMPILER) as run:
            typed = await confirm_form(run)
            (typed_tm,) = fake.mandates
            before = (await run.get("/api/cards/CA0001/passport")).json()
            refuse_the_run(fake)

            r = await start(run, policy="scenario")
            assert r.status_code == 503
            assert r.json()["error"] == {"code": "upstream_unavailable", "message": NOTHING_CHANGED, "detail": REFUSED}
            assert not fake.runs
            assert run.services.worker is not None and not run.services.worker.status().runs

            # the scenario's mandate was registered, then revoked at the platform
            (new_tm,) = [tm for tm in fake.mandates if tm != typed_tm]
            assert fake.mandates[new_tm]["instruction"] == SCEN0000
            assert fake.mandates[new_tm]["status"] == "revoked"

            # the card's own policy is active, unchanged, and nothing else was stored
            policy = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (policy["mandate_id"], policy["status"]) == (typed["mandate_id"], "active")
            assert (stored(run, typed["mandate_id"]).note, stored(run, typed["mandate_id"]).revoked_at) == (None, None)
            with session(run.services.db_engine) as s:
                assert [m.mandate_id for m in s.query(Mandate).where(Mandate.card_id == "CA0001")] == [typed["mandate_id"]]
            bound = run.services.worker._policies[typed_tm]
            assert (bound.mandate_id, bound.status) == (typed["mandate_id"], "active")

            # its passport, re-issued, is the one it had: still valid, no new version
            after = (await run.get("/api/cards/CA0001/passport")).json()
            assert (after["passport_id"], after["version"]) == (before["passport_id"], before["version"])
            assert after["document"]["revoked_at"] is None

            # the platform's other word is passed on the same way; the card's policy decides as before
            r = await start(run)
            assert r.status_code == 503 and r.json()["error"]["message"] == NOTHING_CHANGED

    asyncio.run(scenario())


def test_a_refused_run_on_a_card_with_no_policy_leaves_it_with_none(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast(check_instruction=True))
        async with running(db_url, fake=fake, **REAL_COMPILER) as run:
            refuse_the_run(fake)
            assert run.faulty is not None
            run.faulty.fail[("DELETE", "/v1/mandates")] = 503  # the revocation is refused too
            r = await start(run, policy="scenario")
            assert r.status_code == 503
            assert r.json()["error"] == {"code": "upstream_unavailable", "message": NOTHING_CHANGED, "detail": REFUSED}
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"] is None
            assert (await run.get("/api/cards/CA0001/passport")).status_code == 404
            (new_tm,) = fake.mandates
            assert fake.mandates[new_tm]["status"] == "active"  # the DELETE did not get through ...

            # ... and both refusals are kept, newest first
            def refusals() -> list[tuple[str | None, int | None, str | None, str | None]] | None:
                rows = worker_events.latest(run.services.db_engine, "platform_refusal", limit=10)
                return [(e.action, e.status, e.code, e.mandate_id) for e in rows] if len(rows) >= 2 else None

            assert await until(refusals) == [
                ("delete_mandate", 503, "unavailable", new_tm),
                ("create_run", 409, "instruction_mismatch", new_tm),
            ]

    asyncio.run(scenario())


def test_a_card_with_no_policy_gets_the_scenarios_and_a_served_one_is_used_verbatim(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast(check_instruction=True, **judging_pack()))
        served = fake.instruction_of("SCEN9001")
        assert served != SCEN0000  # the served catalogue's own words
        async with running(db_url, fake=fake, **REAL_COMPILER) as run:
            assert (await run.get("/api/cards/CA9001/policy")).json()["mandate"] is None
            # without the option the card still needs a policy of its own
            r = await start(run, "SCEN9001", "CA9001")
            assert r.status_code == 409 and r.json()["error"]["code"] == "validation"

            r = await start(run, "SCEN9001", "CA9001", policy="scenario")
            assert r.status_code == 200, r.text
            live = r.json()
            platform = live["platform_mandate"]
            assert platform["registered_for_run"] is True
            assert "replaced_mandate_id" not in platform and "previous_viseca_mandate_id" not in platform
            assert fake.mandates[platform["viseca_mandate_id"]]["instruction"] == served
            policy = (await run.get("/api/cards/CA9001/policy")).json()["mandate"]
            assert (policy["mandate_id"], policy["instruction"]) == (live["mandate_id"], served)
            passport = (await run.get("/api/cards/CA9001/passport")).json()
            assert [v["reason"] for v in passport["versions"]] == ["operator_run"]

            # the served scenario's run is followed to the end and decided under that policy
            async def done() -> bool:
                return (await run.get(f"/api/dev/runs/{live['run_id']}")).json()["state"] == "done"

            await until(done)
            (decision,) = await run.decisions("CU9001")
            assert decision["run_id"] == live["ledger_run_id"]
            assert "no_active_policy" not in decision["reason_codes"]

    asyncio.run(scenario())


def test_a_refusal_before_the_policy_changes_leaves_the_card_as_it_was(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast(check_instruction=True))
        async with running(db_url, fake=fake, **REAL_COMPILER) as run:
            typed = await confirm_form(run)
            assert run.faulty is not None
            run.faulty.fail[("POST", "/v1/mandates")] = 503
            r = await start(run, policy="scenario")
            assert r.status_code == 503
            error = r.json()["error"]
            assert error["message"] == "The payment platform did not accept the new policy; nothing was changed."
            assert error["detail"] == {"platform_status": 503, "platform_code": "unavailable", "platform_message": "injected"}
            policy = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert (policy["mandate_id"], policy["status"]) == (typed["mandate_id"], "active")
            assert not fake.runs

    asyncio.run(scenario())


def test_a_starting_run_names_its_card_and_holder_before_the_first_purchase(db_url: str) -> None:  # noqa: F811
    """D4 and D7 read the worker while a run starts: they name the card D3 learnt from the
    platform (and its holder), not an empty card and "Unknown customer"."""

    async def scenario() -> None:
        async with running(db_url, fake=FakeViseca(fast())) as run:
            await confirm_form(run)
            run.fake.hold()  # the run starts, its first purchase waits until released
            r = await start(run)
            assert r.status_code == 200, r.text
            for path in ("/api/dev/runs/current", f"/api/dev/runs/{r.json()['run_id']}"):
                shown = (await run.get(path)).json()
                assert (shown["state"], shown["delivered"]) == ("starting", 0)
                assert (shown["card_id"], shown["customer_id"], shown["customer_name"]) == ("CA0001", "CU0001", "Alex Meier")
            run.fake.release()

    asyncio.run(scenario())
