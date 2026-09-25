"""The built app at ``/``: index.html is revalidated on every load, hashed assets are not."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI

from oneguard.api import static


def _get(app: FastAPI, *paths: str) -> list[httpx.Response]:
    async def scenario() -> list[httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return [await client.get(path) for path in paths]

    return asyncio.run(scenario())


def test_index_is_no_cache_and_hashed_assets_stay_cacheable(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    static.mount(app, tmp_path)

    root, index, asset = _get(app, "/", "/index.html", "/assets/index-abc123.js")
    assert root.status_code == 200 and root.headers["cache-control"] == "no-cache"
    assert index.status_code == 200 and index.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200 and "cache-control" not in asset.headers


def test_placeholder_is_no_cache(tmp_path: Path) -> None:
    app = FastAPI()
    static.mount(app, tmp_path)  # no index.html: the placeholder page
    (page,) = _get(app, "/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"


def test_ops_and_verify_serve_the_app_page(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    app = FastAPI()
    static.mount(app, tmp_path)
    for path in ("/ops", "/verify"):  # the console; the passport QR link (docs/passport.md)
        (page,) = _get(app, path)
        assert page.status_code == 200 and page.headers["cache-control"] == "no-cache", path
        assert page.headers["content-type"].startswith("text/html"), path
        assert page.text == "<!doctype html><title>app</title>", path
