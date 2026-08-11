"""OIDC authorization-code login endpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import create_session, database_session
from pornarr_api.errors import ErrorResponse
from pornarr_api.oidc import OidcAuthenticationError, discover, exchange_code, validate_id_token
from pornarr_api.routers.auth import set_auth_cookies
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.models.user import User
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
STATE_TTL_SECONDS = 10 * 60
Session = Annotated[AsyncSession, Depends(database_session)]


class OidcStateInvalidError(PornarrError):
    code = "OIDC_STATE_INVALID"
    status = 400


class OidcIdentityNotLinkedError(PornarrError):
    code = "OIDC_IDENTITY_NOT_LINKED"
    status = 403


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
    provider = await session.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404)
    document = await _discovery(provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    await request.app.state.redis.set(
        _state_key(state),
        json.dumps({"provider_id": str(provider.id), "nonce": nonce, "code_verifier": verifier}),
        ex=STATE_TTL_SECONDS,
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
    "/callback",
    status_code=303,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
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
    provider = await session.get(OidcProvider, provider_id)
    if provider is None or not provider.enabled:
        raise OidcAuthenticationError("The OIDC provider is unavailable.")
    document = await _discovery(provider)
    token = await exchange_code(provider, document, code, verifier, _callback_url(request))
    claims = await validate_id_token(token, provider, document, nonce)
    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise OidcAuthenticationError("The provider identity token is invalid.")
    identity = await session.scalar(
        select(OidcIdentity).where(
            OidcIdentity.provider_id == provider.id, OidcIdentity.subject == subject
        )
    )
    user = await session.get(User, identity.user_id) if identity is not None else None
    if user is None or not user.is_active:
        raise OidcIdentityNotLinkedError("The OIDC identity is not linked to an active account.")
    session_token, csrf_token = await create_session(request, user)
    response = RedirectResponse(f"{request.app.state.settings.base_path}/", status_code=303)
    set_auth_cookies(response, request, session_token, csrf_token)
    return response
