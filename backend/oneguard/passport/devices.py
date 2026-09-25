"""Device binding (docs/passport.md, "Devices"): only a device enrolled on a card may
change what that card's agent is allowed to do.

A device is a P-256 key pair made in the browser (WebCrypto, not extractable); OneGuard
keeps the public key (JWK). A device-bound request carries four headers:

    X-OneGuard-Device     the device id the card's enrolment returned
    X-OneGuard-Ts         Unix time in seconds when the request was signed
    X-OneGuard-Nonce      a random string, never reused by that device
    X-OneGuard-Signature  base64 ECDSA-P256-SHA256 over
                          canonical({"method", "path", "body", "ts", "nonce"})

``body`` is the request's JSON body (``null`` when there is none), ``path`` the URL path
without the query, ``ts`` the header's integer. The signature may be raw ``r || s`` (64
bytes, what WebCrypto makes) or DER. A request is accepted only when the device is
``enrolled`` on the card it acts on, ``|now - ts| <= 120 s`` and the nonce is new.

Enrolment: the first device on a card (none enrolled) is enrolled at once and is the card's
**controller**; any later one waits ``pending`` until the controller approves it, and is then
an **approved** device. Every enrolled device signs policy changes (C2, C4, C5) and step-up
answers (C8); only the controller approves or removes devices and hands control to another
enrolled device (``transfer``); anyone else gets ``not_controller`` (403). The controller
cannot remove itself (it transfers first), so an enrolled card always has one. At least one
enrolled device always remains. The operator reset (``/api/dev/devices/reset/{card}``,
outside prod) removes them all: issuer-side recovery in a real rollout.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError

from oneguard.passport.canonical import canonical
from oneguard.store.db import session
from oneguard.store.schema import Device, DeviceNonce

DEVICE_HEADER = "X-OneGuard-Device"
SIGNATURE_HEADER = "X-OneGuard-Signature"
TS_HEADER = "X-OneGuard-Ts"
NONCE_HEADER = "X-OneGuard-Nonce"
MAX_SKEW_S = 120
NONCE_TTL = timedelta(minutes=10)
LABEL_MAX = 60
DEFAULT_LABEL = "This device"

AuthCode = Literal["device_signature_required", "device_not_enrolled", "signature_invalid", "replay"]


class DeviceAuthError(Exception):
    """A device-bound request that is refused (401): nothing is applied."""

    def __init__(self, code: AuthCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeviceRoleError(Exception):
    """A device change only the card's controller may make, asked by another device (403)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "not_controller"
        self.message = message


class DeviceStateError(Exception):
    """A device change that does not fit the device's state (409)."""

    def __init__(self, code: Literal["last_device", "device_state"], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Keys --------------------------------------------------------------------------------------


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def public_key(jwk: Mapping[str, Any]) -> ec.EllipticCurvePublicKey:
    """A P-256 public key from its JWK (``kty: EC``, ``crv: P-256``, ``x``, ``y``)."""
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("the device key must be an EC P-256 JWK")
    try:
        x, y = _b64url(str(jwk["x"])), _b64url(str(jwk["y"]))
    except (KeyError, binascii.Error) as exc:
        raise ValueError("the device key has no valid x and y") from exc
    if len(x) != 32 or len(y) != 32:
        raise ValueError("the device key's x and y must be 32 bytes each")
    numbers = ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1())
    return numbers.public_key()  # raises ValueError when the point is not on the curve


def public_jwk(jwk: Mapping[str, Any]) -> dict[str, str]:
    """Only the public members of a JWK, validated (anything else is dropped)."""
    public_key(jwk)
    return {"kty": "EC", "crv": "P-256", "x": str(jwk["x"]), "y": str(jwk["y"])}


def thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638 JWK thumbprint (base64url SHA-256): the same key has the same thumbprint."""
    members = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(hashlib.sha256(members.encode()).digest()).rstrip(b"=").decode()


def signed_payload(method: str, path: str, body: Any, ts: int, nonce: str) -> bytes:
    """The bytes a device signs for one request."""
    return canonical({"method": method.upper(), "path": path, "body": body, "ts": ts, "nonce": nonce})


def verify_signature(jwk: Mapping[str, Any], signature_b64: str, payload: bytes) -> bool:
    try:
        raw = base64.b64decode(signature_b64, validate=True)
        if len(raw) == 64:  # WebCrypto: r || s
            raw = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        public_key(jwk).verify(raw, payload, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


# Devices -----------------------------------------------------------------------------------


Role = Literal["controller", "approved"]


@dataclass(frozen=True)
class DeviceView:
    device_id: str
    card_id: str
    customer_id: str
    label: str
    status: str
    enrolled_at: datetime | None
    enrolled_by_device_id: str | None
    removed_at: datetime | None
    last_seen_at: datetime
    role: Role | None = None  # enrolled devices only


def controller_of(rows: list[Device]) -> Device | None:
    """The card's controller among its device rows: the enrolled device that became it last
    (``controller_since``); with none marked (rows from before controllers existed) the
    earliest enrolled device; None when no device is enrolled."""
    live = [r for r in rows if r.status == "enrolled"]
    marked = [r for r in live if r.controller_since is not None]
    if marked:
        return max(marked, key=lambda r: (r.controller_since, r.device_id))
    return min(live, key=lambda r: (r.enrolled_at or r.last_seen_at, r.device_id), default=None)


def _role(row: Device, controller: Device | None) -> Role | None:
    if row.status != "enrolled":
        return None
    return "controller" if controller is not None and row.device_id == controller.device_id else "approved"


def _view(row: Device, controller: Device | None = None) -> DeviceView:
    return DeviceView(
        device_id=row.device_id, card_id=row.card_id, customer_id=row.customer_id, label=row.label,
        status=row.status, enrolled_at=row.enrolled_at, enrolled_by_device_id=row.enrolled_by_device_id,
        removed_at=row.removed_at, last_seen_at=row.last_seen_at, role=_role(row, controller),
    )  # fmt: skip


def _card_rows(s: Any, card_id: str) -> list[Device]:
    return list(s.scalars(select(Device).where(Device.card_id == card_id)))


def _controller_signed(rows: list[Device], by_device_id: str, what: str) -> Device:
    """The card's controller, which must be the signer; else ``not_controller``."""
    controller = controller_of(rows)
    if controller is None or controller.device_id != by_device_id:
        name = f' ("{controller.label}")' if controller is not None else ""
        raise DeviceRoleError(f"Only the device that controls this card{name} can {what}.")
    return controller


def clean_label(label: str | None) -> str:
    """The customer's name for the device: one line, at most 60 characters."""
    text = " ".join((label or "").split())[:LABEL_MAX].strip()
    return text or DEFAULT_LABEL


def devices(db: Engine, card_ids: list[str]) -> list[DeviceView]:
    """Every device on these cards, enrolled first, then pending, then removed; oldest first."""
    if not card_ids:
        return []
    order = {"enrolled": 0, "pending": 1, "removed": 2}
    with session(db) as s:
        rows = list(s.scalars(select(Device).where(Device.card_id.in_(sorted(set(card_ids))))))
    by_card: dict[str, list[Device]] = {}
    for r in rows:
        by_card.setdefault(r.card_id, []).append(r)
    controllers = {card: controller_of(card_rows) for card, card_rows in by_card.items()}
    views = [_view(r, controllers[r.card_id]) for r in rows]
    return sorted(views, key=lambda d: (order.get(d.status, 3), d.enrolled_at or d.last_seen_at, d.device_id))


def device(db: Engine, card_id: str, device_id: str) -> DeviceView:
    """One device on the card with its role (KeyError when the card has no such device)."""
    found = next((d for d in devices(db, [card_id]) if d.device_id == device_id), None)
    if found is None:
        raise KeyError(device_id)
    return found


def enrolled(db: Engine, card_id: str) -> list[DeviceView]:
    return [d for d in devices(db, [card_id]) if d.status == "enrolled"]


def enrol(db: Engine, card_id: str, customer_id: str, jwk: Mapping[str, Any], label: str | None, now: datetime) -> tuple[DeviceView, bool]:
    """Register a device key on a card: ``(device, changed)``.

    The same key on the same card returns its existing device (a removed one enrols
    again as new). The card's first device is enrolled at once as its controller; others
    are pending until the controller approves them.
    """
    public = public_jwk(jwk)
    print_ = thumbprint(public)
    with session(db) as s:
        rows = _card_rows(s, card_id)
        for row in rows:
            if row.status != "removed" and thumbprint(row.public_key_jwk) == print_:
                return _view(row, controller_of(rows)), False
        first = not any(r.status == "enrolled" for r in rows)
        row = Device(
            device_id=str(uuid.uuid4()),
            card_id=card_id,
            customer_id=customer_id,
            label=clean_label(label),
            public_key_jwk=public,
            status="enrolled" if first else "pending",
            enrolled_at=now if first else None,
            enrolled_by_device_id=None,
            removed_at=None,
            last_seen_at=now,
            controller_since=now if first else None,
        )
        s.add(row)
        s.flush()
        return _view(row, controller_of([*rows, row])), True


def _target(rows: list[Device], device_id: str) -> Device:
    row = next((r for r in rows if r.device_id == device_id), None)
    if row is None:
        raise KeyError(device_id)
    return row


def approve(db: Engine, card_id: str, device_id: str, by_device_id: str, now: datetime) -> DeviceView:
    """The controller (``by_device_id``) lets a pending device sign for the card."""
    with session(db) as s:
        rows = _card_rows(s, card_id)
        row = _target(rows, device_id)
        controller = _controller_signed(rows, by_device_id, "approve devices")
        if row.status != "pending":
            raise DeviceStateError("device_state", f"This device is {row.status}, not waiting for approval.")
        row.status = "enrolled"
        row.enrolled_at = now
        row.enrolled_by_device_id = by_device_id
        s.flush()
        return _view(row, controller)


def remove(db: Engine, card_id: str, device_id: str, by_device_id: str, now: datetime) -> DeviceView:
    """The controller removes a pending or approved device. It cannot remove itself: with
    other devices enrolled it transfers control first (409 ``device_state``); alone, it is
    the card's last device (409 ``last_device``)."""
    with session(db) as s:
        rows = _card_rows(s, card_id)
        row = _target(rows, device_id)
        controller = _controller_signed(rows, by_device_id, "remove devices")
        if row.status == "removed":
            raise DeviceStateError("device_state", "This device is already removed.")
        if row.device_id == controller.device_id:
            if any(r.status == "enrolled" and r.device_id != row.device_id for r in rows):
                raise DeviceStateError(
                    "device_state", "This device controls the card; make another device the controller first."
                )
            raise DeviceStateError(
                "last_device", "This is the only device that controls this card; approve another one first."
            )
        row.status = "removed"
        row.removed_at = now
        s.flush()
        return _view(row, controller)


def transfer(db: Engine, card_id: str, device_id: str, by_device_id: str, now: datetime) -> DeviceView:
    """The controller hands control to another enrolled device, which becomes the controller;
    the old controller stays enrolled as an approved device."""
    with session(db) as s:
        rows = _card_rows(s, card_id)
        row = _target(rows, device_id)
        controller = _controller_signed(rows, by_device_id, "hand over control")
        if row.status != "enrolled":
            raise DeviceStateError("device_state", f"This device is {row.status}; approve it first.")
        if row.device_id == controller.device_id:
            raise DeviceStateError("device_state", "This device already controls the card.")
        for other in rows:
            other.controller_since = None
        row.controller_since = now
        s.flush()
        return _view(row, row)


def reset(db: Engine, card_id: str, now: datetime) -> int:
    """Operator recovery: every device on the card removed (the next one enrols as first)."""
    with session(db) as s:
        rows = list(s.scalars(select(Device).where(Device.card_id == card_id, Device.status != "removed")))
        for row in rows:
            row.status = "removed"
            row.removed_at = now
            row.controller_since = None
        return len(rows)


# Requests ----------------------------------------------------------------------------------


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name) or headers.get(name.lower())
    return value.strip() if value and value.strip() else None


def verify_request(
    db: Engine,
    card_id: str,
    method: str,
    path: str,
    body: Any,
    headers: Mapping[str, str],
    now: datetime,
) -> str:
    """The id of the enrolled device that signed this request for ``card_id``.

    Raises ``DeviceAuthError`` (401) when the headers are missing, the device is not
    enrolled on the card, the signature does not match, the time is off by more than
    120 s, or the nonce was seen before. The nonce is kept only for a valid request.
    """
    device_id, signature = _header(headers, DEVICE_HEADER), _header(headers, SIGNATURE_HEADER)
    ts_raw, nonce = _header(headers, TS_HEADER), _header(headers, NONCE_HEADER)
    if not (device_id and signature and ts_raw and nonce):
        raise DeviceAuthError(
            "device_signature_required", "This change must be signed by a device that controls this card."
        )
    if len(nonce) > 128:
        raise DeviceAuthError("signature_invalid", "The request's nonce is too long.")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise DeviceAuthError("signature_invalid", "The request's time is not a whole number of seconds.") from None
    with session(db) as s:
        row = s.get(Device, device_id)
        if row is None or row.card_id != card_id or row.status != "enrolled":
            raise DeviceAuthError("device_not_enrolled", "This device isn't approved for this card yet.")
        jwk = dict(row.public_key_jwk)
    if not verify_signature(jwk, signature, signed_payload(method, path, body, ts, nonce)):
        raise DeviceAuthError("signature_invalid", "The device signature does not match this request.")
    if abs(now.timestamp() - ts) > MAX_SKEW_S:
        raise DeviceAuthError("replay", "The signed request is too old (or from the future); sign it again.")
    try:
        with session(db) as s:
            s.execute(delete(DeviceNonce).where(DeviceNonce.seen_at < now - NONCE_TTL))
            s.add(DeviceNonce(device_id=device_id, nonce=nonce, seen_at=now))
            device = s.get(Device, device_id)
            if device is not None:
                device.last_seen_at = now
    except IntegrityError:
        raise DeviceAuthError("replay", "This signed request was already used.") from None
    return device_id
