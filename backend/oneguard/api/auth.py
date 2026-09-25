"""Device-bound writes (docs/api-contract.md §3.10, docs/passport.md "Devices").

C2 confirm, C4 tighten, C5 revoke, C8 resolve and the device approve / remove calls are
accepted only when signed by a device enrolled on the card they act on. The check runs
before anything is read for the change or applied, so a refused request (401) changes
nothing. C1 drafts and every GET stay open.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from oneguard.api.errors import ApiError
from oneguard.api.services import Services
from oneguard.passport import devices as device_store
from oneguard.passport.devices import DeviceAuthError


async def _body(request: Request) -> Any:
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError(422, "validation", "The request body is not valid JSON.") from None


async def require_device(request: Request, s: Services, card_id: str) -> str:
    """The id of the enrolled device on ``card_id`` that signed this request, else 401."""
    body = await _body(request)
    try:
        return await s.db(
            device_store.verify_request,
            s.db_engine,
            card_id,
            request.method,
            request.url.path,
            body,
            request.headers,
            s.now(),
        )
    except DeviceAuthError as exc:
        raise ApiError(401, exc.code, exc.message, {"card_id": card_id}) from None
