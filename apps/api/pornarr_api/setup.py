"""Guard an unconfigured instance so only setup endpoints are reachable."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from pornarr_api.spa import is_api_path
from pornarr_db.models.user import User

# `/api/health` is exempt on purpose. installation.md and backup.md tell an
# operator to verify a deployment by reading a health report, and an unconfigured
# instance is exactly when the mounts that report checks are most likely to be
# wrong. It exposes nothing a configured instance keeps back either — the report
# is unauthenticated there too — so exempting it widens no boundary. `/health`
# needs no entry: it is not an API path, and neither is the web application.
_ALLOWED = frozenset(
    {
        "/api/health",
        "/api/setup/status",
        "/api/setup/validate-library-path",
        "/api/setup/test-indexer",
        "/api/setup/test-download-client",
        "/api/setup/complete",
        "/api/openapi.json",
    }
)


class SetupMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # Only the API is gated. The wizard that completes setup is a client
        # route served from `index.html`, so gating the document and its assets
        # locks an operator out of the one screen that can unlock the instance.
        if not is_api_path(request.url.path) or request.url.path in _ALLOWED:
            return await call_next(request)
        engine = getattr(request.app.state, "engine", None)
        if not isinstance(engine, AsyncEngine):
            return await call_next(request)
        async with AsyncSession(engine) as session:
            configured = await session.scalar(select(User.id).limit(1)) is not None
        if not configured:
            return JSONResponse(
                status_code=503, content={"code": "SETUP_REQUIRED", "status": 503, "context": {}}
            )
        return await call_next(request)
