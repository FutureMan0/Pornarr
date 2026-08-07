"""Static asset serving and the single-page-application fallback.

The web build is compiled into the image and served from here, so production runs
no Node process. Anything under `/api` is the API; everything else is a client
route and resolves to `index.html`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from pornarr_api.errors import error_body

STATIC_ROOT = Path(__file__).parent / "static"


def mount_spa(app: FastAPI, static_root: Path | None = None) -> None:
    """Serve built assets and fall back to index.html for client routes.

    Registered last, so every real route wins over the fallback.
    """
    root = static_root or STATIC_ROOT
    index = root / "index.html"

    if root.is_dir():
        assets = root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str) -> Response:
        # An unknown /api path is an API error, not a client route. Returning
        # index.html here would hand a JSON client an HTML page and turn a 404
        # into a parse error.
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content=error_body("NOT_FOUND", 404))

        if index.is_file():
            return FileResponse(index)

        # The frontend has not been built into this image. Say so, rather than
        # serving a 404 that looks like a routing bug.
        return JSONResponse(
            status_code=503,
            content=error_body("WEB_ASSETS_MISSING", 503, expected_path=str(index)),
        )
