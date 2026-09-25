"""Passport, receipts, verification and devices (docs/api-contract.md §1.3, docs/passport.md).

GETs and ``POST /api/verify`` are open: anyone may check a document. Enrolling a device is
open too (the card's first device is enrolled at once, any later one waits for approval);
approving or removing one needs a signature from a device already enrolled on the card.
"""

from __future__ import annotations

import io
import os
from typing import Any

import segno
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from oneguard.api import models as api
from oneguard.api import queries
from oneguard.api.auth import require_device
from oneguard.api.errors import ApiError, not_found
from oneguard.api.routes_customer import reply, services
from oneguard.api.services import Services
from oneguard.passport import devices as device_store
from oneguard.passport.book import PassportBook
from oneguard.passport.canonical import as_signed
from oneguard.passport.devices import DeviceRoleError, DeviceStateError, DeviceView
from oneguard.store.schema import Passport, Receipt

router = APIRouter(prefix="/api")

PUBLIC_URL_ENV = "ONEGUARD_PUBLIC_URL"
DEFAULT_PUBLIC_URL = "https://oneguard.fly.dev"


def public_url() -> str:
    """Where the verify page lives (the passport QR code points there)."""
    return (os.environ.get(PUBLIC_URL_ENV, "").strip() or DEFAULT_PUBLIC_URL).rstrip("/")


def book(s: Services) -> PassportBook:
    if s.book is None:
        raise ApiError(503, "upstream_unavailable", "Passports are not available yet; try again in a moment.")
    return s.book


async def card_customer(s: Services, card_id: str) -> str:
    customer_id = await s.db(queries.card_customer, s.db_engine, card_id)
    if customer_id is None:
        raise not_found(f"No card {card_id}.")
    return customer_id


def device_model(d: DeviceView) -> api.Device:
    return api.Device(
        device_id=d.device_id, card_id=d.card_id, label=d.label, status=d.status,  # type: ignore[arg-type]
        enrolled_at=d.enrolled_at, enrolled_by_device_id=d.enrolled_by_device_id,
        removed_at=d.removed_at, last_seen_at=d.last_seen_at, role=d.role,
    )  # fmt: skip


def passport_model(latest: Passport, versions: list[Passport]) -> api.Passport:
    return api.Passport(
        passport_id=latest.passport_id,
        version=latest.version,
        document=as_signed(latest.document),
        signature=latest.signature,
        key_id=latest.key_id,
        versions=[api.PassportVersion(version=v.version, issued_at=v.issued_at, reason=v.reason) for v in versions],
    )


def receipt_model(row: Receipt) -> api.Receipt:
    return api.Receipt(
        receipt_id=row.receipt_id,
        document=as_signed(row.document),
        signature=row.signature,
        key_id=row.key_id,
        history=[api.ReceiptSignature.model_validate({**h, "document": as_signed(h["document"])}) for h in row.history],
    )


# Keys, passports, receipts -------------------------------------------------------------------


@router.get("/passport/keys", response_model=api.KeysResponse)
async def keys(request: Request) -> JSONResponse:
    """OneGuard's public signing keys, the active one first."""
    ring = book(services(request)).keys
    return reply(
        api.KeysResponse(
            keys=[
                api.PublicKey(key_id=k.key_id, algorithm="ed25519", public_key_pem=k.public_key_pem, active=k.active)
                for k in ring.public_keys()
            ]
        )
    )


async def _card_passport(s: Services, card_id: str) -> tuple[Passport, list[Passport]]:
    await card_customer(s, card_id)
    found = await s.db(book(s).card_passport, card_id)
    if found is None:
        raise not_found(f"Card {card_id} has no passport: confirm a policy first.")
    return found


@router.get("/cards/{card_id}/passport", response_model=api.Passport)
async def card_passport(card_id: str, request: Request) -> JSONResponse:
    """The card's current passport, latest version, with every version's reason."""
    latest, versions = await _card_passport(services(request), card_id)
    return reply(passport_model(latest, versions))


@router.get("/cards/{card_id}/passport/qr.svg")
async def card_passport_qr(card_id: str, request: Request) -> Response:
    """A QR code of the verify link for the card's latest passport version."""
    latest, _ = await _card_passport(services(request), card_id)
    link = f"{public_url()}/verify?passport={latest.passport_id}&v={latest.version}"
    return Response(qr_svg(link), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


def qr_svg(link: str) -> bytes:
    """A standalone SVG document (XML declaration, ``xmlns``), so it renders as an image."""
    out = io.BytesIO()
    segno.make(link, error="m").save(out, kind="svg", scale=4, border=2, dark="#111827", light="#ffffff", title=link)
    return out.getvalue()


@router.get("/authorizations/{authorization_id}/receipt", response_model=api.Receipt)
async def receipt(authorization_id: str, request: Request) -> JSONResponse:
    """The decision's signed receipt (issued on the spot if the sweep has not yet)."""
    s = services(request)
    row = await s.db(book(s).receipt, authorization_id)
    if row is None:
        raise not_found(f"No purchase {authorization_id}.")
    return reply(receipt_model(row))


# Verify ------------------------------------------------------------------------------------


def _kind(document: dict[str, Any]) -> str | None:
    return {"oneguard.passport/1": "passport", "oneguard.receipt/1": "receipt"}.get(str(document.get("type")))


def _verified(b: PassportBook, document: dict[str, Any], signature: str, key_id: str, current: bool | None) -> api.VerifyResult:
    valid, reason = b.verify(document, signature, key_id)
    kind = _kind(document)
    issued = document.get("issued_at") if kind == "passport" else document.get("decided_at")
    return api.VerifyResult(
        valid=valid,
        document_type=kind,  # type: ignore[arg-type]
        key_id=key_id,
        issued_at=issued if isinstance(issued, str) else None,
        reason=reason,
        document=as_signed(document) if valid else document,
        current=current if kind == "passport" else None,
    )


def _verify_stored(b: PassportBook, body: api.VerifyRequest) -> api.VerifyResult:
    if body.receipt_id is not None:
        row = b.receipt_by_id(body.receipt_id)
        if row is None:
            raise not_found(f"No receipt {body.receipt_id}.")
        return _verified(b, row.document, row.signature, row.key_id, None)
    assert body.passport_id is not None
    found = b.passport(body.passport_id)
    if found is None:
        raise not_found(f"No passport {body.passport_id}.")
    latest, versions = found
    chosen = latest if body.version is None else next((v for v in versions if v.version == body.version), None)
    if chosen is None:
        raise not_found(f"Passport {body.passport_id} has no version {body.version}.")
    current = chosen.version == latest.version and chosen.revoked_at is None
    result = _verified(b, chosen.document, chosen.signature, chosen.key_id, current)
    if result.valid and not current:
        state = "revoked" if latest.revoked_at is not None else f"superseded by version {latest.version}"
        result = result.model_copy(update={"reason": f"{result.reason} This passport is {state}."})
    return result


@router.post("/verify", response_model=api.VerifyResult)
async def verify(body: api.VerifyRequest, request: Request) -> JSONResponse:
    """Check a passport or receipt against OneGuard's keys: one posted with its signature,
    or a stored one by id (the QR link's ``passport`` and ``v``, or a ``receipt_id``)."""
    s = services(request)
    b = book(s)
    if body.document is not None:
        assert body.signature is not None and body.key_id is not None
        return reply(await s.db(_verified, b, body.document, body.signature, body.key_id, None))
    return reply(await s.db(_verify_stored, b, body))


# Devices -----------------------------------------------------------------------------------


async def _reissue(s: Services, card_id: str) -> None:
    """The card's passport lists its enrolled devices: a new version when they changed."""
    await s.db(book(s).sync_passports, card_id=card_id, first_reason="confirmed")


@router.get("/cards/{card_id}/devices", response_model=api.DevicesResponse)
async def list_devices(card_id: str, request: Request) -> JSONResponse:
    """Every device on the card, with its status (enrolled, pending, removed)."""
    s = services(request)
    await card_customer(s, card_id)
    rows = await s.db(device_store.devices, s.db_engine, [card_id])
    return reply(api.DevicesResponse(devices=[device_model(d) for d in rows]))


@router.post("/cards/{card_id}/devices", response_model=api.EnrolDeviceResponse)
async def enrol_device(card_id: str, body: api.EnrolDeviceRequest, request: Request) -> JSONResponse:
    """Register this device's public key on the card. The card's first device is enrolled
    at once; any later one is ``pending`` until an enrolled device approves it. The same key
    again returns the same device."""
    s = services(request)
    customer_id = await card_customer(s, card_id)
    try:
        device, changed = await s.db(
            device_store.enrol, s.db_engine, card_id, customer_id, body.public_key_jwk, body.label, s.now()
        )
    except ValueError as exc:
        raise ApiError(422, "validation", str(exc)) from None
    if changed and device.status == "enrolled":
        await _reissue(s, card_id)
    return reply(api.EnrolDeviceResponse(device_id=device.device_id, status=device.status))  # type: ignore[arg-type]


_CHANGES = {"approve": device_store.approve, "remove": device_store.remove, "transfer": device_store.transfer}


async def _change_device(request: Request, card_id: str, device_id: str, action: str) -> JSONResponse:
    """P8-P10: signed by an enrolled device (401 otherwise), which must be the card's
    controller (403 ``not_controller``); a new passport version when it changed who may sign."""
    s = services(request)
    await card_customer(s, card_id)
    signer = await require_device(request, s, card_id)
    try:
        device = await s.db(_CHANGES[action], s.db_engine, card_id, device_id, signer, s.now())
    except KeyError:
        raise not_found(f"No device {device_id} on card {card_id}.") from None
    except DeviceRoleError as exc:
        raise ApiError(403, exc.code, exc.message, {"card_id": card_id}) from None
    except DeviceStateError as exc:
        raise ApiError(409, exc.code, exc.message) from None
    await _reissue(s, card_id)
    return reply(device_model(device))


@router.post("/cards/{card_id}/devices/{device_id}/approve", response_model=api.Device)
async def approve_device(card_id: str, device_id: str, request: Request) -> JSONResponse:
    """The card's controller lets a pending device sign for it (a new passport version)."""
    return await _change_device(request, card_id, device_id, "approve")


@router.post("/cards/{card_id}/devices/{device_id}/remove", response_model=api.Device)
async def remove_device(card_id: str, device_id: str, request: Request) -> JSONResponse:
    """The card's controller removes a pending or approved device; it cannot remove itself (409)."""
    return await _change_device(request, card_id, device_id, "remove")


@router.post("/cards/{card_id}/devices/{device_id}/transfer", response_model=api.Device)
async def transfer_control(card_id: str, device_id: str, request: Request) -> JSONResponse:
    """The card's controller makes another enrolled device the controller (a new passport version)."""
    return await _change_device(request, card_id, device_id, "transfer")
