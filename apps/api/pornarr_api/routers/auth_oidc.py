"""OIDC authorization-code login endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import SESSION_COOKIE, create_session, database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_api.oidc import OidcAuthenticationError, discover, exchange_code, validate_id_token
from pornarr_api.oidc_mapping import link_oidc_identity, resolve_oidc_user
from pornarr_api.routers.auth import set_auth_cookies
from pornarr_db.models.oidc import OidcProvider
from pornarr_db.models.user import User
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
STATE_TTL_SECONDS = 10 * 60
Session = Annotated[AsyncSession, Depends(database_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class OidcStateInvalidError(PornarrError):
    code = "OIDC_STATE_INVALID"
    status = 400


class OidcProviderSummary(BaseModel):
    """What the sign-in screen may know before anybody has a session.

    An id and a name, and nothing that belongs to `/api/admin/oidc`: not the
    issuer, not the client id, and not a disabled provider, which this instance
    has chosen not to offer and should not have to explain to a stranger.
    """

    id: UUID
    name: str


@router.get("/providers", response_model=list[OidcProviderSummary])
async def list_enabled_providers(session: Session) -> list[OidcProviderSummary]:
    """The buttons `/login` gets to draw. Reachable without an account, by
    design: that screen is where this is needed and nobody there has a
    session yet.
    """
    providers = await session.scalars(
        select(OidcProvider).where(OidcProvider.enabled).order_by(OidcProvider.name)
    )
    return [OidcProviderSummary(id=provider.id, name=provider.name) for provider in providers]


def _state_key(state: str) -> str:
    return f"pornarr:auth:oidc:{state}"


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _callback_url(request: Request) -> str:
    return str(request.url_for("oidc_callback"))


async def _discovery(provider: OidcProvider) -> dict[str, Any]:
    if provider.discovery_document is not None:
        return provider.discovery_document
    return await discover(provider.issuer)


async def _start_authorization(
    provider_id: UUID, request: Request, session: AsyncSession, *, link_user: User | None = None
) -> RedirectResponse:
    provider = await session.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404)
    document = await _discovery(provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    state_data: dict[str, str] = {
        "provider_id": str(provider.id),
        "nonce": nonce,
        "code_verifier": verifier,
    }
    if link_user is not None:
        session_token = request.cookies.get(SESSION_COOKIE)
        if session_token is None:
            raise OidcStateInvalidError("The OIDC link state is invalid.")
        state_data["link_user_id"] = str(link_user.id)
        state_data["link_session_token"] = session_token
    await request.app.state.redis.set(
        _state_key(state), json.dumps(state_data), ex=STATE_TTL_SECONDS
    )
    parameters = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": _callback_url(request),
        "scope": " ".join(provider.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{document['authorization_endpoint']}?{urlencode(parameters)}")


@router.get(
    "/{provider_id}/login",
    status_code=307,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def oidc_login(
    provider_id: UUID,
    request: Request,
    session: Session,
) -> RedirectResponse:
    return await _start_authorization(provider_id, request, session)


@router.get(
    "/{provider_id}/link",
    status_code=307,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def oidc_link(
    provider_id: UUID, request: Request, session: Session, user: CurrentUser
) -> RedirectResponse:
    return await _start_authorization(provider_id, request, session, link_user=user)


@router.get(
    "/callback",
    status_code=303,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def oidc_callback(
    code: str,
    state: str,
    request: Request,
    session: Session,
) -> RedirectResponse:
    stored = await request.app.state.redis.getdel(_state_key(state))
    try:
        state_data = json.loads(stored)
        provider_id = UUID(state_data["provider_id"])
        nonce = state_data["nonce"]
        verifier = state_data["code_verifier"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OidcStateInvalidError("The OIDC login state is invalid or has expired.") from exc
    if not isinstance(nonce, str) or not isinstance(verifier, str):
        raise OidcStateInvalidError("The OIDC login state is invalid or has expired.")
    link_user_id = state_data.get("link_user_id")
    link_session_token = state_data.get("link_session_token")
    if (link_user_id is None) != (link_session_token is None):
        raise OidcStateInvalidError("The OIDC link state is invalid.")
    if link_user_id is not None:
        if not isinstance(link_user_id, str) or not isinstance(link_session_token, str):
            raise OidcStateInvalidError("The OIDC link state is invalid.")
        try:
            expected_user_id = UUID(link_user_id)
        except ValueError as exc:
            raise OidcStateInvalidError("The OIDC link state is invalid.") from exc
    provider = await session.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        raise OidcAuthenticationError("The OIDC provider is unavailable.")
    document = await _discovery(provider)
    token = await exchange_code(provider, document, code, verifier, _callback_url(request))
    claims = await validate_id_token(token, provider, document, nonce)
    if link_user_id is not None:
        session_token = request.cookies.get(SESSION_COOKIE)
        if session_token is None or not hmac.compare_digest(session_token, link_session_token):
            raise OidcStateInvalidError("The OIDC link state is invalid.")
        user = await get_current_user(request, session)
        if user.id != expected_user_id:
            raise OidcStateInvalidError("The OIDC link state is invalid.")
        await link_oidc_identity(session, provider, claims, user)
        return RedirectResponse(f"{request.app.state.settings.base_path}/", status_code=303)
    user = await resolve_oidc_user(session, provider, claims)
    session_token, csrf_token = await create_session(request, user)
    response = RedirectResponse(f"{request.app.state.settings.base_path}/", status_code=303)
    set_auth_cookies(response, request, session_token, csrf_token)
    return response
