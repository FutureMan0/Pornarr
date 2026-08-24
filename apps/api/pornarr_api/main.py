"""Application factory.

Settings are injected rather than imported at module scope, so a test can build
an application without a real environment and two applications in one process
cannot fight over configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from pornarr_api.auth import SESSION_COOKIE, enforce_csrf, get_current_user, get_streaming_user
from pornarr_api.errors import register_error_handlers
from pornarr_api.lifespan import lifespan
from pornarr_api.middleware import RequestIdMiddleware
from pornarr_api.routers.account import event_router as account_event_router
from pornarr_api.routers.account import oidc_router as account_oidc_router
from pornarr_api.routers.account import router as account_router
from pornarr_api.routers.account_storage import router as account_storage_router
from pornarr_api.routers.admin_audit import router as admin_audit_router
from pornarr_api.routers.admin_download_clients import router as admin_download_clients_router
from pornarr_api.routers.admin_filters import router as admin_filters_router
from pornarr_api.routers.admin_indexers import router as admin_indexers_router
from pornarr_api.routers.admin_library import router as admin_library_router
from pornarr_api.routers.admin_metadata import router as admin_metadata_router
from pornarr_api.routers.admin_oidc import router as admin_oidc_router
from pornarr_api.routers.admin_overview import router as admin_overview_router
from pornarr_api.routers.admin_performance import router as admin_performance_router
from pornarr_api.routers.admin_quality import router as admin_quality_router
from pornarr_api.routers.admin_quarantine import router as admin_quarantine_router
from pornarr_api.routers.admin_settings import router as admin_settings_router
from pornarr_api.routers.admin_tags import router as admin_tags_router
from pornarr_api.routers.admin_users import router as admin_users_router
from pornarr_api.routers.auth import router as auth_router
from pornarr_api.routers.auth_oidc import router as auth_oidc_router
from pornarr_api.routers.collections import router as collections_router
from pornarr_api.routers.comments import admin_router as admin_comments_router
from pornarr_api.routers.comments import router as comments_router
from pornarr_api.routers.events import router as events_router
from pornarr_api.routers.health import dependency_health
from pornarr_api.routers.health import router as health_router
from pornarr_api.routers.invites import admin_router as admin_invites_router
from pornarr_api.routers.invites import router as invites_router
from pornarr_api.routers.library import media_router as library_media_router
from pornarr_api.routers.library import router as library_router
from pornarr_api.routers.metrics import router as metrics_router
from pornarr_api.routers.monitors import router as monitors_router
from pornarr_api.routers.notifications import router as notifications_router
from pornarr_api.routers.peers import proxy_router as peers_proxy_router
from pornarr_api.routers.peers import router as admin_peers_router
from pornarr_api.routers.playback import progress_router as playback_progress_router
from pornarr_api.routers.playback import router as playback_router
from pornarr_api.routers.profile import router as profile_router
from pornarr_api.routers.queue import router as queue_router
from pornarr_api.routers.ratings import router as ratings_router
from pornarr_api.routers.recommendations import router as recommendations_router
from pornarr_api.routers.requests import router as requests_router
from pornarr_api.routers.scenes import router as scenes_router
from pornarr_api.routers.search import router as search_router
from pornarr_api.routers.sends import router as sends_router
from pornarr_api.routers.setup import router as setup_router
from pornarr_api.routers.shorts import admin_router as admin_shorts_router
from pornarr_api.routers.shorts import router as shorts_router
from pornarr_api.routers.stream import router as stream_router
from pornarr_api.routers.transcode import admin_router as admin_transcode_router
from pornarr_api.routers.transcode import router as transcode_router
from pornarr_api.routers.watchlist import router as watchlist_router
from pornarr_api.setup import SetupMiddleware
from pornarr_api.spa import mount_spa
from pornarr_integrations.newznab import NewznabAdapter
from pornarr_integrations.qbittorrent import QbittorrentAdapter
from pornarr_integrations.sabnzbd import SabnzbdAdapter
from pornarr_integrations.torznab import TorznabAdapter
from pornarr_shared.config import Settings, get_settings

API_PREFIX = "/api"

# The document is the boundary the TypeScript client and the MSW handlers are
# generated from (ADR 0009), and it declared no authentication at all: a client
# generated strictly from it could not know that anything on the instance was
# protected, or which of the three documented paths to use. api-contract.md names
# the paths; these are the same two the code enforces, written where a generator
# can read them. OIDC is deliberately absent -- it establishes one of these
# sessions and is not a scheme a caller presents.
SECURITY_SCHEMES: dict[str, dict[str, str]] = {
    "SessionCookie": {
        "type": "apiKey",
        "in": "cookie",
        "name": SESSION_COOKIE,
        "description": (
            "Opaque session established by POST /api/auth/login or by completing OIDC. "
            "Unsafe methods additionally require the X-CSRF-Token double-submit header."
        ),
    },
    "ApiKey": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Api-Key",
        "description": (
            "Per-user key from POST /api/account/api-keys. Valid on /api/* except "
            "/api/auth/*, and needs no CSRF token."
        ),
    },
}


def requires_authentication(route: RouteContext) -> bool:
    """Whether anything in this route's dependency tree resolves a user.

    Read off the dependants rather than kept as a second list, because a list
    would drift: `require_role` reaches `get_current_user` through a closure, and
    a route added without touching this file must still be described correctly.
    """
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call in (get_current_user, get_streaming_user):
            return True
        pending.extend(dependant.dependencies)
    return False


def route_security(path: str) -> list[dict[str, list[str]]]:
    """Either credential, except under /api/auth, which refuses API keys."""
    if path.startswith(f"{API_PREFIX}/auth"):
        return [{"SessionCookie": []}]
    return [{"SessionCookie": []}, {"ApiKey": []}]


class PornarrApi(FastAPI):
    """FastAPI, plus the one thing it cannot derive: how to authenticate.

    `iter_route_contexts` is the same walk `get_openapi` itself does, so the
    routes described here are exactly the operations in the document; an included
    router is one entry in `self.routes` holding its own, and walking the top
    level alone finds one route out of a hundred and seventy-seven. Calling
    `super()` first keeps FastAPI's caching and its invalidation, and decorating
    an already-cached document assigns the same values again.
    """

    def openapi(self) -> dict[str, Any]:
        document = super().openapi()
        document.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
        paths = document.get("paths", {})
        for route in iter_route_contexts(self.routes):
            if not isinstance(route.original_route, APIRoute) or not requires_authentication(route):
                continue
            path = route.path_format
            operations = paths.get(path, {}) if path is not None else {}
            for method in route.methods or ():
                operation = operations.get(method.lower())
                if operation is not None:
                    operation["security"] = route_security(path or "")
        return document


def stable_operation_id(route: APIRoute) -> str:
    """Derive an operation id from the tag and the function name.

    FastAPI's default appends the path and method, so renaming a route or adding
    a path parameter renames the generated TypeScript symbol. That produces a
    frontend diff for a backend change that altered no behaviour.
    """
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


api_router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(enforce_csrf)])
api_router.include_router(auth_router)
api_router.include_router(auth_oidc_router)
api_router.include_router(account_router)
api_router.include_router(account_oidc_router)
api_router.include_router(account_event_router)
api_router.include_router(admin_download_clients_router)
api_router.include_router(admin_filters_router)
api_router.include_router(account_storage_router)
api_router.include_router(admin_oidc_router)
api_router.include_router(admin_performance_router)
api_router.include_router(admin_quality_router)
api_router.include_router(admin_overview_router)
api_router.include_router(admin_quarantine_router)
api_router.include_router(admin_audit_router)
api_router.include_router(admin_indexers_router)
api_router.include_router(admin_metadata_router)
api_router.include_router(admin_library_router)
api_router.include_router(admin_peers_router)
api_router.include_router(admin_settings_router)
api_router.include_router(admin_tags_router)
api_router.include_router(admin_users_router)
api_router.include_router(admin_invites_router)
api_router.include_router(invites_router)
api_router.include_router(admin_comments_router)
api_router.include_router(admin_shorts_router)
api_router.include_router(events_router)
api_router.include_router(health_router)
api_router.include_router(library_router)
api_router.include_router(library_media_router)
api_router.include_router(metrics_router)
api_router.include_router(monitors_router)
api_router.include_router(notifications_router)
api_router.include_router(playback_router)
api_router.include_router(playback_progress_router)
api_router.include_router(peers_proxy_router)
api_router.include_router(queue_router)
api_router.include_router(requests_router)
api_router.include_router(recommendations_router)
api_router.include_router(search_router)
api_router.include_router(setup_router)
api_router.include_router(stream_router)
api_router.include_router(transcode_router)
api_router.include_router(admin_transcode_router)
api_router.include_router(profile_router)
api_router.include_router(ratings_router)
api_router.include_router(comments_router)
api_router.include_router(shorts_router)
api_router.include_router(sends_router)
api_router.include_router(collections_router)
api_router.include_router(scenes_router)
api_router.include_router(watchlist_router)


def create_app(
    settings: Settings | None = None,
    *,
    static_root: Path | None = None,
) -> FastAPI:
    resolved = settings or get_settings()

    app = PornarrApi(
        title="Pornarr",
        version="0.0.0",
        # Sub-path deployment behind a reverse proxy: the application must not
        # assume it is served from the domain root.
        root_path=resolved.base_path,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if resolved.app_env != "production" else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        generate_unique_id_function=stable_operation_id,
    )
    app.state.settings = resolved
    app.state.download_client_adapters = {
        "qbittorrent": QbittorrentAdapter(),
        "sabnzbd": SabnzbdAdapter(),
    }
    # Never registered, so testing an indexer's connection answered "no adapter
    # is installed" for the only two implementations there are.
    app.state.indexer_adapters = {
        "torznab": TorznabAdapter(),
        "newznab": NewznabAdapter(),
    }

    app.add_middleware(SetupMiddleware)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(api_router)
    # The container probe and the address the operations documentation tells
    # operators to curl, answering the same report as `/api/health` rather than a
    # literal. `docker-compose.yml` runs `curl -fsS .../health` and backup.md ends
    # a restore with `curl --fail .../health`; both of those are verifications, and
    # a handler that returns 200 unconditionally verifies nothing. It stays out of
    # the schema — `/api/health` is the documented one — and outside `/api`, so
    # `SetupMiddleware` and the CSRF dependency leave it alone.
    app.add_api_route("/health", dependency_health, methods=["GET"], include_in_schema=False)

    # Mounts /assets and records where index.html lives. The SPA fallback itself
    # is a 404 handler, so adding routers after this call is safe.
    mount_spa(app, static_root)

    return app
