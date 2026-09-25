"""The built frontend at ``/``, mounted after every API route (Appendix A).

``frontend/dist`` (or ``ONEGUARD_FRONTEND_DIST``) is served as static files with
``index.html`` at ``/`` and at ``/verify`` (the passport QR link; the app reads the query). HTML pages carry ``Cache-Control: no-cache`` so a browser picks up
a new deploy on the next load; the hashed ``/assets`` files stay cacheable. Without a build, ``/`` answers with a one-line placeholder
page. Unknown ``/api/…`` paths always answer with the JSON error envelope, never HTML.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from oneguard.api.errors import not_found

DIST_ENV = "ONEGUARD_FRONTEND_DIST"
DEFAULT_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
PLACEHOLDER = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>OneGuard</title>"
    "<p>OneGuard is running; the app is not built. The API is at <code>/api</code>.</p></html>"
)


class AppFiles(StaticFiles):
    """The built app; ``index.html`` names the hashed bundles, so it is revalidated on every load."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


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
        index = dist / "index.html"

        @app.get("/verify", include_in_schema=False)
        async def verify_page() -> FileResponse:
            return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})

        app.mount("/", AppFiles(directory=dist, html=True), name="frontend")
        return

    @app.get("/", include_in_schema=False)
    async def placeholder() -> HTMLResponse:
        return HTMLResponse(PLACEHOLDER, headers={"Cache-Control": "no-cache"})
