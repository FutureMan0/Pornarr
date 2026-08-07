"""Static asset serving and the single-page-application fallback.

The web build is compiled into the image and served from here, so production runs
no Node process. Anything under `/api` is the API; every other unmatched path is a
client route and resolves to `index.html`.

The fallback is a **404 handler**, not a catch-all route. A catch-all route wins
against anything registered after it, which silently kills every router added
later — the kind of ordering trap that is invisible until someone adds a route in
the wrong place.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from pornarr_api.errors import error_body

STATIC_ROOT = Path(__file__).parent / "static"
API_PREFIX = "/api"


def mount_spa(app: FastAPI, static_root: Path | None = None) -> None:
    """Mount built assets and record where `index.html` lives."""
    root = static_root or STATIC_ROOT
    app.state.spa_index = root / "index.html"

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def spa_response(request: Request) -> Response | None:
    """The SPA document for a client route, or None when this is an API path.

    Returning None rather than a 404 keeps the decision in one place: the caller
    is the error handler, and it already knows how to render an API 404.
    """
    if is_api_path(request.url.path):
        return None

    index: Path | None = getattr(request.app.state, "spa_index", None)
    if index is None:
        return None

    if index.is_file():
        return FileResponse(index)

    # The frontend was not built into this image. Say so with the path we looked
    # in, rather than a 404 that reads like a routing bug.
    return JSONResponse(
        status_code=503,
        content=error_body("WEB_ASSETS_MISSING", 503, expected_path=str(index)),
    )
