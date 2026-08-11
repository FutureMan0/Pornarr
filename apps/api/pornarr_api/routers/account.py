"""Session-only management of a user's authentication settings."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import (
    ForbiddenError,
    database_session,
    generate_api_key,
    get_current_user,
    has_local_password,
    hash_password,
)
from pornarr_api.errors import ErrorResponse
from pornarr_db.audit import write_audit
from pornarr_db.models.api_keys import UserApiKey
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.models.user import User
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/account/api-keys", tags=["account"])
oidc_router = APIRouter(prefix="/account/oidc", tags=["account"])
Session = Annotated[AsyncSession, Depends(database_session)]


class ApiKeyWrite(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=128)]


class ApiKeyResponse(BaseModel):
    id: UUID
    label: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    key: str


class OidcIdentityResponse(BaseModel):
    id: UUID
    provider_id: UUID
    provider_name: str


class OidcUnlinkWouldLockAccountError(PornarrError):
    code = "OIDC_UNLINK_WOULD_LOCK_ACCOUNT"
    status = 409


async def session_user(request: Request, user: Annotated[User, Depends(get_current_user)]) -> User:
    if getattr(request.state, "auth_source", None) is not None:
        raise ForbiddenError("API keys cannot manage authentication settings.")
    return user


CurrentUser = Annotated[User, Depends(session_user)]


def key_response(key: UserApiKey) -> ApiKeyResponse:
    return ApiKeyResponse.model_validate(key, from_attributes=True)


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    payload: ApiKeyWrite, user: CurrentUser, session: Session
) -> ApiKeyCreatedResponse:
    plaintext = generate_api_key()
    key = UserApiKey(
        user_id=user.id,
        label=payload.label,
        prefix=plaintext[:12],
        key_hash=hash_password(plaintext),
    )
    session.add(key)
    await session.flush()
    write_audit(session, actor_id=user.id, action="apikey.created", target=str(key.id))
    return ApiKeyCreatedResponse(**key_response(key).model_dump(), key=plaintext)


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(user: CurrentUser, session: Session) -> list[ApiKeyResponse]:
    keys = await session.scalars(
        select(UserApiKey)
        .where(UserApiKey.user_id == user.id)
        .order_by(UserApiKey.created_at.desc())
    )
    return [key_response(key) for key in keys]


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(key_id: UUID, user: CurrentUser, session: Session) -> None:
    key = await session.scalar(
        select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user.id)
    )
    if key is None:
        raise HTTPException(status_code=404)
    await session.delete(key)
    write_audit(session, actor_id=user.id, action="apikey.revoked", target=str(key_id))


@oidc_router.get("", response_model=list[OidcIdentityResponse])
async def list_oidc_identities(user: CurrentUser, session: Session) -> list[OidcIdentityResponse]:
    identities = await session.execute(
        select(OidcIdentity, OidcProvider.name)
        .join(OidcProvider, OidcProvider.id == OidcIdentity.provider_id)
        .where(OidcIdentity.user_id == user.id)
        .order_by(OidcIdentity.created_at)
    )
    return [
        OidcIdentityResponse(id=identity.id, provider_id=identity.provider_id, provider_name=name)
        for identity, name in identities
    ]


@oidc_router.delete("/{identity_id}", status_code=204, responses={409: {"model": ErrorResponse}})
async def unlink_oidc_identity(identity_id: UUID, user: CurrentUser, session: Session) -> None:
    identity = await session.scalar(
        select(OidcIdentity).where(OidcIdentity.id == identity_id, OidcIdentity.user_id == user.id)
    )
    if identity is None:
        raise HTTPException(status_code=404)
    identity_count = await session.scalar(
        select(func.count()).select_from(OidcIdentity).where(OidcIdentity.user_id == user.id)
    )
    if identity_count == 1 and not has_local_password(user):
        raise OidcUnlinkWouldLockAccountError(
            "Unlinking would leave this account without a login method."
        )
    await session.delete(identity)
