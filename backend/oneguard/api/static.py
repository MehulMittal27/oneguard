"""The built frontend at ``/``, mounted after every API route (Appendix A).

``frontend/dist`` (or ``ONEGUARD_FRONTEND_DIST``) is served as static files with
``index.html`` at ``/``. Without a build, ``/`` answers with a one-line placeholder
page. Unknown ``/api/…`` paths always answer with the JSON error envelope, never HTML.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from oneguard.api.errors import not_found

DIST_ENV = "ONEGUARD_FRONTEND_DIST"
DEFAULT_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
PLACEHOLDER = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>OneGuard</title>"
    "<p>OneGuard is running; the app is not built. The API is at <code>/api</code>.</p></html>"
)


def frontend_dist() -> Path:
    override = os.environ.get(DIST_ENV, "").strip()
    return Path(override) if override else DEFAULT_DIST


def mount(app: FastAPI, dist: Path | None = None) -> None:
    """Register last: the API catch-all, then the app (or the placeholder) at ``/``."""

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def unknown_api(path: str) -> None:
        raise not_found(f"No endpoint /api/{path}.")

    dist = dist or frontend_dist()
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
        return

    @app.get("/", include_in_schema=False)
    async def placeholder() -> HTMLResponse:
        return HTMLResponse(PLACEHOLDER)
