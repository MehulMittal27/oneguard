"""A device in Python: what the browser does with WebCrypto, for the CLI and tests.

``DeviceKey`` holds a P-256 key pair, gives its public JWK and signs a request the way
``devices.verify_request`` checks it (docs/passport.md, "Devices"). ``make demo-live``
keeps one key per API host in ``~/.config/oneguard/device-<host>.pem`` and enrols it on
the card it confirms a policy on.
"""

from __future__ import annotations

import base64
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from oneguard.passport.devices import (
    DEVICE_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TS_HEADER,
    signed_payload,
)


def _b64url(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


class DeviceKey:
    def __init__(self, private: ec.EllipticCurvePrivateKey | None = None) -> None:
        self.private = private or ec.generate_private_key(ec.SECP256R1())

    @classmethod
    def load_or_create(cls, path: Path) -> DeviceKey:
        """The key stored at ``path``, created (owner-only) on first use."""
        if path.is_file():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise TypeError(f"{path} is not an EC private key")
            return cls(key)
        device = cls()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            device.private.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        path.chmod(0o600)
        return device

    @property
    def jwk(self) -> dict[str, str]:
        numbers = self.private.public_key().public_numbers()
        return {"kty": "EC", "crv": "P-256", "x": _b64url(numbers.x), "y": _b64url(numbers.y)}

    def sign(self, method: str, path: str, body: Any, *, ts: int | None = None, nonce: str | None = None) -> dict[str, Any]:
        """``(ts, nonce, signature)`` for one request; signature raw ``r || s``, base64."""
        ts = int(time.time()) if ts is None else ts
        nonce = nonce or secrets.token_urlsafe(16)
        der = self.private.sign(signed_payload(method, path, body, ts, nonce), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return {"ts": ts, "nonce": nonce, "signature": base64.b64encode(raw).decode()}

    def headers(self, device_id: str, method: str, path: str, body: Any, **kw: Any) -> dict[str, str]:
        """The four ``X-OneGuard-*`` headers for this request."""
        signed = self.sign(method, path, body, **kw)
        return {
            DEVICE_HEADER: device_id,
            TS_HEADER: str(signed["ts"]),
            NONCE_HEADER: signed["nonce"],
            SIGNATURE_HEADER: signed["signature"],
        }
