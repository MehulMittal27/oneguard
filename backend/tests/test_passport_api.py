"""docs/passport.md through the app: passports, receipts, verify, device-bound writes.

Runs the real lifespan on a seeded SQLite copy with the fake sandbox where a live run is
needed (``tests/test_api_contract.py`` harness). The background sweep is off: each test
calls the book itself, so nothing depends on timing.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from oneguard.passport.devices import (
    DEVICE_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TS_HEADER,
)
from oneguard.passport.signer import DeviceKey
from oneguard.store.db import session
from oneguard.store.schema import Passport, Receipt
from tests.fake_viseca import FakeViseca
from tests.test_api_contract import (  # noqa: F401  fixtures
    Clock,
    Running,
    confirm_form,
    db_url,
    fast,
    live_run,
    running,
    seeded_db,
    until,
)

NO_SWEEP = {"passport_sweep_s": None}


def book(run: Running) -> Any:
    assert run.services.book is not None
    return run.services.book


async def passport(run: Running, card: str = "CA0001") -> dict[str, Any]:
    r = await run.get(f"/api/cards/{card}/passport")
    assert r.status_code == 200, r.text
    return r.json()


async def enrol(run: Running, key: DeviceKey, card: str = "CA0001", label: str = "Second phone") -> dict[str, Any]:
    r = await run.post(f"/api/cards/{card}/devices", json={"public_key_jwk": key.jwk, "label": label})
    assert r.status_code == 200, r.text
    return r.json()


def signed(run: Running, key: DeviceKey, device_id: str, path: str, body: Any = None, **kw: Any) -> dict[str, str]:
    kw.setdefault("ts", int(run.clock().timestamp()))
    return key.headers(device_id, "POST", path, body, **kw)


# Passports ---------------------------------------------------------------------------------


def test_a_passport_is_versioned_on_confirm_tighten_enrol_remove_and_revoke(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            assert (await run.get("/api/cards/CA0001/passport")).status_code == 404  # no policy yet
            mandate = await confirm_form(run, "CA0001", period_limit_chf=None, period_days=None)
            first = await passport(run)
            assert first["version"] == 1 and [v["reason"] for v in first["versions"]] == ["confirmed"]
            doc = first["document"]
            assert doc["type"] == "oneguard.passport/1" and doc["mandate_id"] == mandate["mandate_id"]
            assert doc["holder"]["customer_id"] == "CU0001" and doc["card_id"] == "CA0001"
            assert [c["id"] for c in doc["checks"]] == [c["id"] for c in mandate["checks"]]
            assert [d["device_id"] for d in doc["devices"]] == [run.device_ids["CA0001"]]  # C2's signer
            assert doc["devices"][0]["enrolled_by_device_id"] is None and doc["revoked_at"] is None
            assert mandate["passport"] == {
                "passport_id": first["passport_id"], "version": 1, "issued_at": doc["issued_at"], "devices_count": 1,
            }  # fmt: skip

            # tighten: a check from this card's drafts
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": {
                "per_order_limit_chf": 125, "period_limit_chf": 300, "period_days": 7, "categories": [],
                "sellers_used_before_only": False, "uncertainty_policy": "ask"}})).json()  # fmt: skip
            period = next(c for c in draft["checks"] if c["id"] == "period_limit")
            r = await run.post("/api/cards/CA0001/policy/tighten", json={"add_checks": [period]})
            assert r.status_code == 200, r.text
            assert r.json()["passport"]["version"] == 2

            # a second device: pending changes nothing, approval is a new version, removal another
            second = DeviceKey()
            pending = await enrol(run, second)
            assert pending["status"] == "pending"
            assert (await passport(run))["version"] == 2
            approved = await run.post(f"/api/cards/CA0001/devices/{pending['device_id']}/approve")
            assert approved.status_code == 200, approved.text
            assert approved.json()["enrolled_by_device_id"] == run.device_ids["CA0001"]
            now = await passport(run)
            assert now["version"] == 3 and len(now["document"]["devices"]) == 2
            removed = await run.post(f"/api/cards/CA0001/devices/{pending['device_id']}/remove")
            assert removed.status_code == 200 and removed.json()["status"] == "removed"

            r = await run.post("/api/cards/CA0001/policy/revoke")
            assert r.status_code == 204
            last = await passport(run)
            assert [v["reason"] for v in last["versions"]] == ["confirmed", "tightened", "devices", "devices", "revoked"]
            assert last["document"]["revoked_at"] is not None
            # C3 serves the latest version; every older one is superseded
            c3 = (await run.get("/api/cards/CA0001/policy")).json()["mandate"]
            assert c3["passport"]["version"] == last["version"] == 5
            with session(run.services.db_engine) as s:
                rows = list(s.scalars(select(Passport).order_by(Passport.version)))
            assert [r.superseded_at is not None for r in rows] == [True, True, True, True, False]
            # nothing more changes a revoked passport
            assert await asyncio.to_thread(book(run).sync_passports) == 0
            for row in rows:
                assert book(run).verify(row.document, row.signature, row.key_id)[0]

    asyncio.run(scenario())


def test_a_new_policy_on_the_card_revokes_the_old_passport_and_issues_its_own(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            old = await confirm_form(run, "CA0001")
            new = await confirm_form(run, "CA0001", per_order_limit_chf=60)
            current = await passport(run)
            assert current["document"]["mandate_id"] == new["mandate_id"] and current["version"] == 1
            found = book(run).summaries([old["mandate_id"]])[old["mandate_id"]]
            assert found.version == 2 and found.reason == "revoked" and found.document["revoked_at"]

    asyncio.run(scenario())


def test_the_qr_code_links_to_the_verify_page_of_the_latest_version(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ONEGUARD_PUBLIC_URL", "https://example.test/")

    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            await confirm_form(run, "CA0001")
            r = await run.get("/api/cards/CA0001/passport/qr.svg")
            assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
            assert r.text.lstrip().startswith("<svg") and "<path" in r.text
            import segno

            p = await passport(run)
            expected = segno.make(f"https://example.test/verify?passport={p['passport_id']}&v=1", error="m")
            assert r.text == expected.svg_inline(scale=4, border=2, dark="#111827", light="#ffffff")

    asyncio.run(scenario())


# Verify ------------------------------------------------------------------------------------


def test_verify_accepts_the_document_and_refuses_one_changed_byte(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            await confirm_form(run, "CA0001")
            p = await passport(run)
            keys = (await run.get("/api/passport/keys")).json()["keys"]
            assert [k["key_id"] for k in keys] == [p["key_id"]] and keys[0]["active"] and keys[0]["algorithm"] == "ed25519"

            body = {"document": p["document"], "signature": p["signature"], "key_id": p["key_id"]}
            ok = (await run.post("/api/verify", json=body)).json()
            assert ok["valid"] and ok["document_type"] == "passport" and ok["issued_at"] == p["document"]["issued_at"]

            tampered = copy.deepcopy(body)
            tampered["document"]["checks"][0]["value"] = 999
            bad = (await run.post("/api/verify", json=tampered)).json()
            assert not bad["valid"] and "changed after signing" in bad["reason"]

            unknown = (await run.post("/api/verify", json={**body, "key_id": "ogk_nope"})).json()
            assert not unknown["valid"]

            stored = (await run.post("/api/verify", json={"passport_id": p["passport_id"], "version": 1})).json()
            assert stored["valid"] and stored["current"] and stored["document"]["holder"]["name"]
            assert (await run.post("/api/verify", json={"passport_id": p["passport_id"], "version": 9})).status_code == 404
            assert (await run.post("/api/verify", json={"receipt_id": "rc_nope"})).status_code == 404
            assert (await run.post("/api/verify", json={})).status_code == 422
            both = {**body, "receipt_id": "rc_1"}
            assert (await run.post("/api/verify", json=both)).status_code == 422

    asyncio.run(scenario())


# Receipts ----------------------------------------------------------------------------------


def test_every_decision_gets_a_receipt_and_answers_are_appended(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        clock = Clock()
        fake = FakeViseca(fast())
        async with running(db_url, fake=fake, clock=clock, **NO_SWEEP) as run:
            decisions = await live_run(run)
            counts = await asyncio.to_thread(book(run).sync_receipts)
            assert counts.receipts == 10
            by_outcome: dict[str, list[dict[str, Any]]] = {}
            for d in decisions:
                by_outcome.setdefault(d["decision"], []).append(d)
            assert set(by_outcome) == {"approved", "stopped", "uncertain"}
            p = await passport(run)
            for d in decisions:
                r = await run.get(f"/api/authorizations/{d['authorization_id']}/receipt")
                assert r.status_code == 200, r.text
                receipt = r.json()
                doc = receipt["document"]
                assert receipt["receipt_id"] == d["receipt_id"] == doc["receipt_id"]
                assert doc["outcome"] == {"approved": "approve", "stopped": "decline", "uncertain": "step_up"}[d["decision"]]
                assert (doc["passport_id"], doc["passport_version"]) == (p["passport_id"], p["version"])
                assert doc["authorization"]["billing_amount_chf"] == f"{d['billing_amount_chf']:.2f}"
                assert doc["resolution"] is None and receipt["history"] == []
                verified = (await run.post("/api/verify", json={"receipt_id": receipt["receipt_id"]})).json()
                assert verified["valid"] and verified["document_type"] == "receipt"

            first, second, *_ = by_outcome["uncertain"]
            r = await run.post(f"/api/authorizations/{first['authorization_id']}/resolve", json={"decision": "approve"})
            assert r.status_code == 204, r.text
            receipt = (await run.get(f"/api/authorizations/{first['authorization_id']}/receipt")).json()
            resolution = receipt["document"]["resolution"]
            assert resolution["outcome"] == "approve" and resolution["resolved_by"] == "customer"
            assert resolution["device_id"] == run.device_ids["CA0001"]
            assert len(receipt["history"]) == 1 and receipt["history"][0]["document"]["resolution"] is None
            old = receipt["history"][0]
            assert book(run).verify(old["document"], old["signature"], old["key_id"])[0]  # the old signature still holds
            assert (await run.post("/api/verify", json={"receipt_id": receipt["receipt_id"]})).json()["valid"]
            # the customer's yes is remembered: part of the passport
            assert (await passport(run))["document"]["remembered_confirmations"] == 1

            # the rest lapse (a read closes them, §3.5): the timeout's answer, no device
            clock.offset = timedelta(seconds=90)
            lapsed = {d["authorization_id"]: d for d in await run.decisions()}
            assert lapsed[second["authorization_id"]]["resolved_by"] == "timeout"
            counts = await asyncio.to_thread(book(run).sync_receipts)
            assert counts.resolutions == len(by_outcome["uncertain"]) - 1 and _resolved(run, second)
            receipt = (await run.get(f"/api/authorizations/{second['authorization_id']}/receipt")).json()
            assert receipt["document"]["resolution"]["resolved_by"] == "timeout"
            assert receipt["document"]["resolution"]["device_id"] is None and len(receipt["history"]) == 1

    asyncio.run(scenario())


def _resolved(run: Running, decision: dict[str, Any]) -> bool:
    with session(run.services.db_engine) as s:
        row = s.scalar(select(Receipt).where(Receipt.live_authorization_id == decision["authorization_id"]))
    return row is not None and row.resolved_at is not None


def test_the_would_approve_if_row_is_posted_to_the_platform(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast())
        async with running(db_url, fake=fake, **NO_SWEEP) as run:
            decisions = await live_run(run)
            declined = [d for d in decisions if d["decision"] == "stopped"]
            assert declined and all(d["would_approve_if"] is None for d in declined)  # the test engine gives none
            posted = [body for a in fake.all_auths() for body in a.decisions]
            assert posted and all(
                not any(row.get("kind") == "would_approve_if" for row in body.get("evidence", [])) for body in posted
            )

    asyncio.run(scenario())


def test_posted_evidence_carries_would_approve_if_on_a_decline() -> None:
    from oneguard.engine.types import EvidenceRow
    from oneguard.viseca.worker import posted_evidence

    row = EvidenceRow(rule="amount", outcome="fail", detail="over", source="policy")
    bounds = [{"field": "authorization.billing_amount_chf", "operator": "<=", "value": 400}]
    posted = posted_evidence([row], bounds, "Would approve at CHF 400.00 or less.")
    assert posted[0] == row.model_dump(mode="json")
    assert posted[1] == {
        "kind": "would_approve_if", "rule": "would_approve_if", "outcome": "info", "source": "policy",
        "detail": "Would approve at CHF 400.00 or less.", "would_approve_if": bounds,
    }  # fmt: skip
    assert posted_evidence([row], None, None) == [row.model_dump(mode="json")]


# Device binding ----------------------------------------------------------------------------


def test_device_bound_writes_refuse_every_unsigned_or_wrongly_signed_request(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            await confirm_form(run, "CA0001")
            phone = run.device_ids["CA0001"]
            path = "/api/cards/CA0001/policy/revoke"

            unsigned = await run.post(path, signed=False)
            assert unsigned.status_code == 401 and unsigned.json()["error"]["code"] == "device_signature_required"

            other = DeviceKey()
            pending = await enrol(run, other)
            assert pending["status"] == "pending"
            r = await run.post(path, signed=False, headers=signed(run, other, pending["device_id"], path))
            assert r.status_code == 401 and r.json()["error"]["code"] == "device_not_enrolled"

            forged = signed(run, other, phone, path)  # the phone's id, another key
            r = await run.post(path, signed=False, headers=forged)
            assert r.status_code == 401 and r.json()["error"]["code"] == "signature_invalid"

            wrong_path = signed(run, run.device, phone, "/api/cards/CA0001/policy/tighten")
            r = await run.post(path, signed=False, headers=wrong_path)
            assert r.json()["error"]["code"] == "signature_invalid"

            stale = signed(run, run.device, phone, path, ts=int(run.clock().timestamp()) - 121)
            r = await run.post(path, signed=False, headers=stale)
            assert r.status_code == 401 and r.json()["error"]["code"] == "replay"

            on_other_card = await enrol(run, run.device, card="CA0002")  # CA0002's first device
            elsewhere = signed(run, run.device, on_other_card["device_id"], path)
            r = await run.post(path, signed=False, headers=elsewhere)
            assert r.json()["error"]["code"] == "device_not_enrolled"

            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["status"] == "active"  # nothing applied

            once = signed(run, run.device, phone, path)
            assert (await run.post(path, signed=False, headers=once)).status_code == 204
            again = await run.post(path, signed=False, headers=once)
            assert again.status_code == 401 and again.json()["error"]["code"] == "replay"

    asyncio.run(scenario())


def test_an_unsigned_resolve_is_refused_and_records_nothing(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        fake = FakeViseca(fast())
        async with running(db_url, fake=fake, **NO_SWEEP) as run:
            decisions = await live_run(run)
            step_up = next(d for d in decisions if d["status"] == "pending_human")
            path = f"/api/authorizations/{step_up['authorization_id']}/resolve"
            r = await run.post(path, signed=False, json={"decision": "approve"})
            assert r.status_code == 401 and r.json()["error"]["code"] == "device_signature_required"
            body_swapped = signed(run, run.device, run.device_ids["CA0001"], path, {"decision": "decline"})
            r = await run.post(path, signed=False, json={"decision": "approve"}, headers=body_swapped)
            assert r.status_code == 401 and r.json()["error"]["code"] == "signature_invalid"
            listed = {d["authorization_id"]: d for d in await run.decisions()}
            assert listed[step_up["authorization_id"]]["status"] == "pending_human"
            assert (await run.post(path, json={"decision": "decline"})).status_code == 204

    asyncio.run(scenario())


def test_confirm_needs_a_device_on_the_drafts_card(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            await confirm_form(run, "CA0001")  # CA0001 now has run.device
            draft = (await run.post("/api/cards/CA0001/policy-drafts", json={"form": {
                "per_order_limit_chf": 50, "period_limit_chf": None, "period_days": None, "categories": [],
                "sellers_used_before_only": False, "uncertainty_policy": "ask"}})).json()  # fmt: skip
            path = f"/api/policy-drafts/{draft['draft_id']}/confirm"
            body = {"checks": draft["checks"], "uncertainty_policy": "ask", "open_questions": []}
            stranger = DeviceKey()
            enrolled = await enrol(run, stranger, card="CA0002")
            r = await run.post(path, signed=False, json=body, headers=signed(run, stranger, enrolled["device_id"], path, body))
            assert r.status_code == 401 and r.json()["error"]["code"] == "device_not_enrolled"
            assert (await run.get("/api/cards/CA0001/policy")).json()["mandate"]["checks"] != draft["checks"]
            assert (await run.post(path, json=body)).status_code == 200

    asyncio.run(scenario())


def test_a_second_device_waits_for_the_first_and_the_last_one_stays(db_url: str) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            first = await enrol(run, run.device, label="Stage laptop")
            assert first["status"] == "enrolled"  # the card's first device: no approval
            run.device_ids["CA0001"] = first["device_id"]
            assert await enrol(run, run.device) == first  # the same key again: the same device

            phone, stranger = DeviceKey(), DeviceKey()
            second = await enrol(run, phone, label="  Second\nphone  ")
            assert second["status"] == "pending"
            listed = (await run.get("/api/cards/CA0001/devices")).json()["devices"]
            assert [(d["label"], d["status"]) for d in listed] == [("Stage laptop", "enrolled"), ("Second phone", "pending")]

            # a pending device cannot approve itself, nor can a device of another card
            approve = f"/api/cards/CA0001/devices/{second['device_id']}/approve"
            r = await run.post(approve, signed=False, headers=signed(run, phone, second["device_id"], approve))
            assert r.status_code == 401 and r.json()["error"]["code"] == "device_not_enrolled"
            elsewhere = await enrol(run, stranger, card="CA0002")
            r = await run.post(approve, signed=False, headers=signed(run, stranger, elsewhere["device_id"], approve))
            assert r.status_code == 401

            assert (await run.post(approve)).status_code == 200  # the first device approves
            assert (await run.post(approve)).json()["error"]["code"] == "device_state"  # not pending any more

            # remove the first from the phone; then the phone is the last and stays
            remove_first = f"/api/cards/CA0001/devices/{first['device_id']}/remove"
            r = await run.post(remove_first, signed=False, headers=signed(run, phone, second["device_id"], remove_first))
            assert r.status_code == 200 and r.json()["status"] == "removed"
            remove_phone = f"/api/cards/CA0001/devices/{second['device_id']}/remove"
            r = await run.post(remove_phone, signed=False, headers=signed(run, phone, second["device_id"], remove_phone))
            assert r.status_code == 409 and r.json()["error"]["code"] == "last_device"
            r = await run.post("/api/cards/CA0001/devices/nope/remove", signed=False,
                               headers=signed(run, phone, second["device_id"], "/api/cards/CA0001/devices/nope/remove"))  # fmt: skip
            assert r.status_code == 404

            bad = await run.post("/api/cards/CA0001/devices", json={"public_key_jwk": {"kty": "RSA"}, "label": "x"})
            assert bad.status_code == 422
            assert (await run.post("/api/cards/CA9999/devices", json={"public_key_jwk": phone.jwk})).status_code == 404

    asyncio.run(scenario())


def test_the_operator_reset_works_only_outside_prod(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    async def scenario() -> None:
        async with running(db_url, **NO_SWEEP) as run:
            await confirm_form(run, "CA0001")
            monkeypatch.setenv("ONEGUARD_ENV", "prod")
            r = await run.post("/api/dev/devices/reset/CA0001")
            assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
            assert len((await passport(run))["document"]["devices"]) == 1
            monkeypatch.setenv("ONEGUARD_ENV", "dev")
            r = await run.post("/api/dev/devices/reset/CA0001")
            assert r.status_code == 200 and r.json() == {"card_id": "CA0001", "removed": 1}
            assert (await passport(run))["document"]["devices"] == []
            fresh = await enrol(run, DeviceKey(), label="Recovered phone")
            assert fresh["status"] == "enrolled"  # the next device enrols as the card's first

    asyncio.run(scenario())


def test_the_signature_headers_are_the_documented_names() -> None:
    assert (DEVICE_HEADER, SIGNATURE_HEADER, TS_HEADER, NONCE_HEADER) == (
        "X-OneGuard-Device", "X-OneGuard-Signature", "X-OneGuard-Ts", "X-OneGuard-Nonce",
    )  # fmt: skip
