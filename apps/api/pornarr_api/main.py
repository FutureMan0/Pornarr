"""Application factory.

Settings are injected rather than imported at module scope, so a test can build
an application without a real environment and two applications in one process
cannot fight over configuration.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI

from pornarr_api.errors import register_error_handlers
from pornarr_api.lifespan import lifespan
from pornarr_api.middleware import RequestIdMiddleware
from pornarr_api.spa import mount_spa
from pornarr_shared.config import Settings, get_settings

API_PREFIX = "/api"

api_router = APIRouter(prefix=API_PREFIX)


def create_app(
    settings: Settings | None = None,
    *,
    static_root: Path | None = None,
) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(
        title="Pornarr",
        version="0.0.0",
        # Sub-path deployment behind a reverse proxy: the application must not
        # assume it is served from the domain root.
        root_path=resolved.base_path,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if resolved.app_env != "production" else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.settings = resolved

    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(api_router)

    # Registered last so every real route takes precedence over the fallback.
    mount_spa(app, static_root)

    return app
