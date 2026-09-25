"""The operator gate on ``/api/dev/*`` (docs/api-contract.md §1.2, §3.8).

In production (``ONEGUARD_ENV=prod``, the image default) every operator endpoint needs
the header ``X-OneGuard-Operator`` equal to the server's ``ONEGUARD_OPERATOR_TOKEN``
(a Fly secret), compared in constant time: missing or wrong is ``401 operator_required``.
A production server with no token set refuses every operator call
(``503 operator_unconfigured``) rather than serving them open. Anywhere else the check is
skipped, so local runs and tests need no token. The token is never logged or echoed.

Scripts that call ``/api/dev/*`` (``make demo-live``, ``make demo-offline``) send
``operator_headers()``: the token from their own environment, when it is set.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request

from oneguard.api.errors import ApiError
from oneguard.store.seed import ENV_VAR

OPERATOR_HEADER = "X-OneGuard-Operator"
TOKEN_ENV = "ONEGUARD_OPERATOR_TOKEN"


def _prod() -> bool:
    return os.environ.get(ENV_VAR, "").strip().lower() == "prod"


def operator_headers() -> dict[str, str]:
    """The operator header for a script's ``/api/dev/*`` calls; empty when no token is set."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    return {OPERATOR_HEADER: token} if token else {}


async def require_operator(request: Request) -> None:
    """The ``/api/dev`` router's dependency: passes off production, else checks the header."""
    if not _prod():
        return
    expected = os.environ.get(TOKEN_ENV, "").strip()
    if not expected:
        raise ApiError(
            503,
            "operator_unconfigured",
            f"Operator endpoints are off: this server has no {TOKEN_ENV} set.",
        )
    sent = request.headers.get(OPERATOR_HEADER, "").strip()
    if not sent:
        raise ApiError(401, "operator_required", f"Operator endpoints need the {OPERATOR_HEADER} header.")
    if not hmac.compare_digest(sent.encode(), expected.encode()):
        raise ApiError(401, "operator_required", "The operator token was not accepted.")
