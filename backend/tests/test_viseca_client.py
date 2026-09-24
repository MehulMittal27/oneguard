"""The Viseca client, its call log, the bearer key's secrecy and the live demo, on the fake."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, select

from oneguard.store import seed as seed_module
from oneguard.store.db import make_engine, session
from oneguard.store.schema import EventRaw, Run, VisecaCall
from oneguard.viseca import demo
from oneguard.viseca.client import (
    SUMMARY_LIMIT,
    VisecaClient,
    VisecaError,
    VisecaNotConfigured,
    store_sink,
)
from tests.fake_viseca import FakeConfig, FakeViseca
from tests.test_worker import ALL_STUBS, fake_client, fast

SECRET = "sk-team-DO-NOT-LEAK-7f3a9c41"


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("client") / "seeded.sqlite"
    engine = make_engine(f"sqlite:///{path}")
    seed_module.run(engine=engine)
    engine.dispose()
    return path


@pytest.fixture
def db(seeded_db: Path, tmp_path: Path) -> Engine:
    path = tmp_path / "oneguard.sqlite"
    shutil.copy(seeded_db, path)
    engine = make_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()


def calls(db: Engine) -> list[VisecaCall]:
    with session(db) as s:
        return list(s.scalars(select(VisecaCall).order_by(VisecaCall.called_at, VisecaCall.id)))


def test_every_endpoint_round_trips_and_is_logged(db: Engine) -> None:
    fake = FakeViseca(fast())

    async def scenario() -> None:
        async with fake_client(fake, db) as client:
            assert (await client.healthz())["status"] == "ok"
            limits = (await client.bootstrap())["limits"]
            assert (limits["step_up_timeout_seconds"], limits["decision_timeout_seconds"]) == (60.0, 3.0)
            reference = await client.reference_data()
            assert reference["history"]["rows"] == 4701 and "sha256" not in reference["history"]
            assert (await client.authorization_history_csv()).startswith("authorization_id,")

            rule = {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 20,
                    "currency": "CHF", "scope": "purchase"}  # fmt: skip
            draft = await client.create_mandate("Buy one item.", [rule], "ask", ["guide"], ["q?"])
            mandate = await client.confirm_mandate(draft["draft_id"])
            mandate_id = mandate["mandate_id"]
            assert (await client.get_mandate(mandate_id))["hard_rules"] == [rule]
            patched = await client.patch_mandate(mandate_id, uncertainty_policy="decline")
            assert patched["uncertainty_policy"] == "decline"

            run = await client.create_run("SCEN0000", mandate_id)
            assert (await client.get_run(run["run_id"]))["generated_event_count"] == 1
            envelope = await client.next_decision_request(wait=1)
            assert envelope is not None and envelope["run_id"] == run["run_id"]
            live_id = envelope["data"]["authorization"]["authorization_id"]
            accepted = await client.post_decision(
                live_id, "step_up", reason_codes=["customer_confirmation"], customer_message="Please review."
            )
            assert accepted is not None and accepted["status"] == "pending_step_up"
            assert accepted["decision"]["decision_source"] == "team" and accepted["step_up_expires_at"]
            again = await client.next_decision_request(wait=0.1)
            assert again is not None and (again["status"], again["event_id"]) == (
                "pending_step_up", envelope["event_id"],
            )  # fmt: skip
            with pytest.raises(VisecaError) as twice:
                await client.post_decision(live_id, "decline")
            assert (twice.value.status, twice.value.code) == (409, "step_up_resolution_required")
            resolved = await client.resolve(live_id, "decline", "The customer declined this purchase.")
            assert resolved is not None and resolved["status"] == "declined"
            assert await client.next_decision_request(wait=0.1) is None
            listed = await client.list_authorizations()
            assert [(a["authorization_id"], a["status"]) for a in listed] == [(live_id, "declined")]
            feed = await client.events(since=0)
            assert [(e["type"], e["status"]) for e in feed["events"]] == [
                ("authorization.request", "awaiting_decision"),
                ("authorization.decision", "pending_step_up"),
                ("authorization.decision", "declined"),
                ("scenario.completed", "completed"),
            ]
            assert (await client.events(since=feed["next_cursor"]))["events"] == []
            assert (await client.delete_mandate(mandate_id))["status"] == "revoked"
            assert (await client.reset_team())["reset"] is True
            assert fake.resets == [{"confirmed": True}]

    asyncio.run(scenario())
    rows = calls(db)
    assert [(r.method, r.path.split("?")[0]) for r in rows[:4]] == [
        ("GET", "/healthz"),
        ("GET", "/v1/bootstrap"),
        ("GET", "/v1/reference-data"),
        ("GET", "/v1/reference-data/authorization-history.csv"),
    ]
    assert rows[3].response_summary.startswith("<text/csv") and rows[3].response_summary.endswith("bytes>")
    polls = [r for r in rows if r.path.startswith("/v1/decision-requests/next")]
    assert [r.status_code for r in polls] == [200, 200, 204] and polls[2].response_summary is None
    assert polls[0].path == "/v1/decision-requests/next?wait=1"
    assert all(r.latency_ms >= 0 for r in rows)
    assert [r.error for r in rows if r.error] == [
        "step_up_resolution_required: Resolve the pending step-up through the /resolve endpoint"
    ]
    assert len(rows) == 21


def test_error_envelopes_become_viseca_errors(db: Engine) -> None:
    fake = FakeViseca(fast())

    async def scenario() -> None:
        async with fake_client(fake, db) as client:
            with pytest.raises(VisecaError) as missing:
                await client.confirm_mandate("draft_nope")
            assert (missing.value.status, missing.value.code) == (404, "not_found")
            draft = await client.create_mandate("x", [], "ask")
            mandate = await client.confirm_mandate(draft["draft_id"])
            with pytest.raises(VisecaError) as loosen:
                await client.patch_mandate(mandate["mandate_id"], uncertainty_policy="approve")
            assert (loosen.value.status, loosen.value.code) == (409, "loosening_not_allowed")
            with pytest.raises(VisecaError) as invalid:
                await client._request("POST", "/v1/team/reset", body={})
            assert (invalid.value.status, invalid.value.code) == (422, "validation_error")
            assert invalid.value.detail[0]["loc"] == ["body", "confirmed"]
        async with fake_client(FakeViseca(fast(reset_enabled=False)), db) as client:
            with pytest.raises(VisecaError) as disabled:
                await client.reset_team()
            assert (disabled.value.status, disabled.value.code) == (403, "reset_disabled")
        async with fake_client(fake, db, key="wrong") as client:
            with pytest.raises(VisecaError) as denied:
                await client.bootstrap()
            assert (denied.value.status, denied.value.code) == (401, "unauthorized")
        async with fake_client(fake, db, key="") as client:
            assert (await client.healthz())["status"] == "ok"
            with pytest.raises(VisecaNotConfigured):
                await client.bootstrap()
        unreachable = VisecaClient("http://127.0.0.1:9", "k", sink=store_sink(db), timeout_s=1)
        async with unreachable:
            with pytest.raises(VisecaError) as down:
                await unreachable.healthz()
            assert (down.value.status, down.value.code) == (None, "upstream_unavailable")

    asyncio.run(scenario())
    errors = [r for r in calls(db) if r.error]
    assert [r.status_code for r in errors] == [404, 409, 422, 403, 401, None]
    assert errors[0].error == "not_found: unknown draft"


def test_logged_bodies_are_capped_at_4_kb(db: Engine) -> None:
    fake = FakeViseca(fast())

    async def scenario() -> None:
        async with fake_client(fake, db) as client:
            await client.create_mandate("x" * 20_000, [], "ask")

    asyncio.run(scenario())
    (row,) = calls(db)
    assert len(row.request_summary) == SUMMARY_LIMIT and len(row.response_summary) == SUMMARY_LIMIT
    assert row.request_summary.endswith("more chars]")


def test_the_bearer_key_never_leaves_the_authorization_header(
    db: Engine, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeViseca(fast(api_key=SECRET, human_window_s=0.3))
    raised: list[str] = []

    async def scenario() -> None:
        async with fake_client(fake, db) as client:
            assert SECRET not in repr(client)
            code = await demo.run_demo(
                client, "SCEN0000", db=db, poll_wait_s=0.2, max_seconds=15, out=print, **ALL_STUBS
            )
            assert code == 0
        async with fake_client(fake, db, key=SECRET + "x") as wrong:
            with pytest.raises(VisecaError) as denied:
                await wrong.bootstrap()
            raised.append(str(denied.value))

    asyncio.run(scenario())
    assert f"Bearer {SECRET}" in fake.authorization_headers  # it was sent, in the header
    out, err = capsys.readouterr()
    with session(db) as s:
        stored = [
            repr([getattr(row, c.name) for c in row.__table__.columns])
            for model in (VisecaCall, EventRaw, Run)
            for row in s.scalars(select(model))
        ]
    assert len(stored) > 10
    for text in [caplog.text, out, err, *raised, *stored]:
        assert SECRET not in text


def test_demo_refuses_to_start_a_run_while_runs_are_switched_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VISECA_API_KEY", "some-key")
    monkeypatch.setenv("ONEGUARD_ALLOW_RUNS", "false")
    monkeypatch.setattr(demo, "get_engine", lambda: pytest.fail("nothing may start"))
    assert demo.main(["--scenario", "SCEN0000"]) == 2
    err = capsys.readouterr().err
    assert "ONEGUARD_ALLOW_RUNS=false" in err and "nothing was started" in err


@pytest.mark.parametrize("value", [None, "true", "1"])
def test_demo_runs_are_allowed_unless_the_flag_is_false(
    value: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if value is None:
        monkeypatch.delenv("ONEGUARD_ALLOW_RUNS", raising=False)
    else:
        monkeypatch.setenv("ONEGUARD_ALLOW_RUNS", value)
    monkeypatch.delenv("VISECA_API_KEY", raising=False)
    assert demo.main(["--scenario", "SCEN0000"]) == 2
    err = capsys.readouterr().err
    assert "VISECA_API_KEY is not set" in err and "ONEGUARD_ALLOW_RUNS" not in err  # past the flag


def test_demo_without_a_key_exits_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("VISECA_API_KEY", raising=False)
    assert demo.main(["--scenario", "SCEN0000"]) == 2
    err = capsys.readouterr().err
    assert "VISECA_API_KEY is not set" in err and "make demo-live" in err


def test_demo_compiles_confirms_runs_and_tails_a_scenario(db: Engine) -> None:
    fake = FakeViseca(FakeConfig(decision_deadline_s=3, human_window_s=0.3, max_wait_s=0.2))
    lines: list[str] = []

    async def scenario() -> int:
        async with fake_client(fake, db) as client:
            return await demo.run_demo(
                client, "SCEN0001", db=db, poll_wait_s=0.2, max_seconds=30, out=lines.append, **ALL_STUBS
            )

    assert asyncio.run(scenario()) == 0
    (mandate,) = fake.mandates.values()
    assert mandate["instruction"] == fake.pack.scenarios["SCEN0001"]["cardholder_instruction"]
    assert lines[0] == f"Instruction: {mandate['instruction']}"
    assert any(line.startswith("Compiled (fallback)") for line in lines)
    assert "progress: 0/10 decided, 0 waiting for the customer" in lines  # generated_event_count
    assert sum("uncertain/expired" in line for line in lines) == 10
    assert lines[-1] == "Summary: {'step_up/expired': 10}"


def test_drain_returns_when_a_finished_summary_was_never_discarded() -> None:
    """A finished call-log future whose done callback never ran must not spin ``drain``."""

    async def scenario() -> None:
        client = VisecaClient("http://x", SECRET)
        finished = asyncio.get_running_loop().create_future()
        finished.set_result(None)
        client._pending_logs.add(finished)  # as if its discard callback had not run
        await asyncio.wait_for(client.drain(), timeout=1)
        assert not client._pending_logs
        await client.aclose()

    asyncio.run(scenario())


def test_httpx_transport_errors_are_not_raised_raw(db: Engine) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async def scenario() -> None:
        client = VisecaClient("http://x", SECRET, transport=httpx.MockTransport(boom), sink=store_sink(db))
        async with client:
            with pytest.raises(VisecaError) as timed_out:
                await client.next_decision_request(wait=1)
        assert timed_out.value.code == "upstream_unavailable" and SECRET not in str(timed_out.value)

    asyncio.run(scenario())
    (row,) = calls(db)
    assert row.status_code is None and row.error.startswith("ReadTimeout")


def test_drain_returns_when_every_summary_is_already_written() -> None:
    """A finished write whose done callback has not run yet must not make drain spin."""

    async def scenario() -> None:
        client = VisecaClient("http://fake-viseca", "key")
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        client._pending_logs.add(done)
        done.add_done_callback(client._pending_logs.discard)  # scheduled, not yet run
        await asyncio.wait_for(client.drain(), 1.0)
        assert not client._pending_logs
        await client.aclose()

    asyncio.run(scenario())
