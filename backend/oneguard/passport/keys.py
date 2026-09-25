"""OneGuard's signing key (docs/passport.md, "Keys").

One Ed25519 key is active and signs every passport and receipt; its public half is served
at ``GET /api/passport/keys``. The first start on an empty store creates it. Keys are never
deleted, so a document signed by an older key still verifies against that key's id.

A signature is ``base64(Ed25519(canonical(document)))``; ``key_id`` is ``ogk_`` + the first
16 hex digits of the SHA-256 of the raw 32-byte public key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from oneguard.passport.canonical import canonical
from oneguard.store.db import session
from oneguard.store.schema import SigningKey

log = logging.getLogger(__name__)

ALGORITHM = "ed25519"


def key_id_of(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"ogk_{hashlib.sha256(raw).hexdigest()[:16]}"


def _public_pem(public: Ed25519PublicKey) -> str:
    return public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _load_public(pem: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("not an Ed25519 public key")
    return key


def _load_private(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("not an Ed25519 private key")
    return key


@dataclass(frozen=True)
class PublicKey:
    key_id: str
    algorithm: str
    public_key_pem: str
    active: bool


class KeyRing:
    """The active signing key and every public key, loaded from ``signing_keys``."""

    def __init__(self, db: Engine, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._db = db
        self._now = now
        self._lock = threading.Lock()
        self._active: tuple[str, Ed25519PrivateKey] | None = None
        self._public: dict[str, PublicKey] = {}

    @classmethod
    def load_or_create(cls, db: Engine, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> KeyRing:
        ring = cls(db, now)
        ring.load()
        return ring

    def load(self) -> None:
        """Read the keys; create the active one when the store has none.

        Two processes starting on an empty store may both create one: the oldest active
        key wins and the others are marked inactive, so exactly one stays active.
        """
        with self._lock:
            rows = self._rows()
            if not any(r.active for r in rows):
                self._create()
                rows = self._rows()
            active = sorted((r for r in rows if r.active), key=lambda r: (r.created_at, r.key_id))
            if len(active) > 1:
                self._deactivate([r.key_id for r in active[1:]])
                rows = self._rows()
            self._public = {
                r.key_id: PublicKey(r.key_id, r.algorithm, r.public_key_pem, r.active) for r in rows
            }
            winner = next(r for r in rows if r.active)
            self._active = (winner.key_id, _load_private(winner.private_key_pem))

    def _rows(self) -> list[SigningKey]:
        with session(self._db) as s:
            return list(s.scalars(select(SigningKey).order_by(SigningKey.created_at, SigningKey.key_id)))

    def _create(self) -> None:
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        row = SigningKey(
            key_id=key_id_of(public),
            algorithm=ALGORITHM,
            public_key_pem=_public_pem(public),
            private_key_pem=private.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ).decode(),
            created_at=self._now(),
            active=True,
        )
        try:
            with session(self._db) as s:
                s.add(row)
        except IntegrityError:  # another process created the same id: impossible in practice
            log.warning("signing key %s already stored", row.key_id)
        log.info("created OneGuard signing key %s", row.key_id)

    def _deactivate(self, key_ids: list[str]) -> None:
        with session(self._db) as s:
            for row in s.scalars(select(SigningKey).where(SigningKey.key_id.in_(key_ids))):
                row.active = False

    @property
    def active_key_id(self) -> str:
        assert self._active is not None
        return self._active[0]

    def sign(self, document: dict[str, Any]) -> tuple[str, str]:
        """``(signature_b64, key_id)`` over ``canonical(document)`` with the active key."""
        assert self._active is not None
        key_id, private = self._active
        return base64.b64encode(private.sign(canonical(document))).decode(), key_id

    def verify(self, document: dict[str, Any], signature: str, key_id: str) -> bool:
        """True when ``signature`` is ``key_id``'s signature of ``canonical(document)``."""
        known = self._public.get(key_id)
        if known is None:
            self.load()  # a key another process created since
            known = self._public.get(key_id)
        if known is None:
            return False
        try:
            raw = base64.b64decode(signature, validate=True)
            _load_public(known.public_key_pem).verify(raw, canonical(document))
        except (InvalidSignature, binascii.Error, ValueError, TypeError):
            return False
        return True

    def knows(self, key_id: str) -> bool:
        return key_id in self._public

    def public_keys(self) -> list[PublicKey]:
        return sorted(self._public.values(), key=lambda k: (not k.active, k.key_id))
