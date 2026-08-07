"""Administrator-only OIDC provider configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_api.oidc import discover
from pornarr_db.models.oidc import OidcProvider
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/admin/oidc", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class ProviderWrite(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    issuer: Annotated[str, Field(min_length=1, max_length=512)]
    client_id: Annotated[str, Field(min_length=1, max_length=512)]
    client_secret: SecretStr
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    enabled: bool = True


class ProviderResponse(BaseModel):
    id: UUID
    name: str
    issuer: str
    client_id: str
    scopes: list[str]
    enabled: bool
    discovery_fetched_at: datetime | None


def provider_response(provider: OidcProvider) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        issuer=provider.issuer,
        client_id=provider.client_id,
        scopes=provider.scopes,
        enabled=provider.enabled,
        discovery_fetched_at=provider.discovery_fetched_at,
    )


async def provider_or_404(session: AsyncSession, provider_id: UUID) -> OidcProvider:
    provider = await session.get(OidcProvider, provider_id)
    if provider is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)
    return provider


@router.get("", response_model=list[ProviderResponse])
async def list_providers(_: Admin, session: Session) -> list[ProviderResponse]:
    return [provider_response(provider) for provider in await session.scalars(select(OidcProvider))]


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(payload: ProviderWrite, _: Admin, session: Session) -> ProviderResponse:
    provider = OidcProvider(
        name=payload.name,
        issuer=payload.issuer.rstrip("/"),
        client_id=payload.client_id,
        client_secret=payload.client_secret.get_secret_value(),
        scopes=payload.scopes,
        enabled=payload.enabled,
    )
    session.add(provider)
    await session.flush()
    return provider_response(provider)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: UUID, _: Admin, session: Session) -> None:
    await session.delete(await provider_or_404(session, provider_id))


@router.post("/{provider_id}/test", response_model=ProviderResponse)
async def test_provider(provider_id: UUID, _: Admin, session: Session) -> ProviderResponse:
    provider = await provider_or_404(session, provider_id)
    provider.discovery_document = await discover(provider.issuer)
    provider.discovery_fetched_at = datetime.now(UTC)
    return provider_response(provider)
