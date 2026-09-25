"""The error envelope of docs/api-contract.md §3.8 for every failure ``/api`` returns.

``ApiError`` is raised by route code; the handlers below turn it, request validation
failures and anything unexpected into ``{ error: { code, message, detail? } }``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from oneguard.api.models import ErrorBody, ErrorCode, ErrorResponse

log = logging.getLogger(__name__)

_STATUS_CODES: dict[int, ErrorCode] = {
    404: "not_found",
    405: "validation",
    422: "validation",
    503: "upstream_unavailable",
}


class ApiError(Exception):
    """A contract error: HTTP status, §3.8 code, a sentence for people, optional detail."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        detail: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail
        self.headers = headers


def not_found(message: str) -> ApiError:
    return ApiError(404, "not_found", message)


def upstream_unavailable(message: str, detail: dict[str, Any] | None = None) -> ApiError:
    return ApiError(503, "upstream_unavailable", message, detail)


def platform_detail(exc: Any) -> dict[str, Any]:
    """A refused platform call (``VisecaError``) as a 503's ``detail``: the platform's HTTP
    status and code, and its own message verbatim when it answered (no status: it did not,
    and the message is ours)."""
    detail: dict[str, Any] = {"platform_status": exc.status, "platform_code": exc.code}
    if exc.status is not None:
        detail["platform_message"] = exc.message
    return detail


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    detail: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, detail=detail))
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def _validation_detail(exc: RequestValidationError) -> dict[str, Any]:
    errors = [
        {k: v for k, v in error.items() if k in ("loc", "msg", "type")} for error in exc.errors()
    ]
    return {"errors": jsonable_encoder(errors)}


def install(app: FastAPI) -> None:
    """Register the envelope handlers on ``app``."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "validation", "The request body is not valid.", _validation_detail(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "internal")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(500, "internal", "Something went wrong on our side.")
