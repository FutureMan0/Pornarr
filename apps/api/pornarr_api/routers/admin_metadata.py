"""Administrator-only scene-metadata provider configuration.

The cascade in the import worker has always accepted provider adapters and
nothing ever built one, because there was nowhere to put a key. Every import
therefore fell back to the file name. These routes are that missing place.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.models.metadata_provider import MetadataProvider
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/admin/metadata-providers", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

# The adapters that exist. An unknown name is a validation failure rather than
# a row that quietly configures nothing.
Implementation = Literal["stashdb", "tpdb"]


class MetadataProviderWrite(BaseModel):
    implementation: Implementation
    endpoint: Annotated[str | None, Field(max_length=512)] = None
    api_key: SecretStr
    priority: int = 0
    enabled: bool = True


class MetadataProviderResponse(BaseModel):
    id: UUID
    implementation: str
    endpoint: str | None
    priority: int
    enabled: bool


def provider_response(provider: MetadataProvider) -> MetadataProviderResponse:
    """The key is never returned: it goes in, and only the worker reads it."""

    return MetadataProviderResponse(
        id=provider.id,
        implementation=provider.implementation,
        endpoint=provider.endpoint,
        priority=provider.priority,
        enabled=provider.enabled,
    )


@router.get("", response_model=list[MetadataProviderResponse])
async def list_metadata_providers(_: Admin, session: Session) -> list[MetadataProviderResponse]:
    providers = await session.scalars(
        select(MetadataProvider).order_by(
            MetadataProvider.priority, MetadataProvider.implementation
        )
    )
    return [provider_response(provider) for provider in providers]


@router.post("", response_model=MetadataProviderResponse, status_code=201)
async def configure_metadata_provider(
    payload: MetadataProviderWrite, _: Admin, session: Session
) -> MetadataProviderResponse:
    # One row per implementation, so configuring it twice replaces the key
    # rather than leaving the cascade to ask the same service twice.
    provider = await session.scalar(
        select(MetadataProvider).where(MetadataProvider.implementation == payload.implementation)
    )
    if provider is None:
        provider = MetadataProvider(implementation=payload.implementation)
        session.add(provider)
    provider.endpoint = payload.endpoint
    provider.api_key = payload.api_key.get_secret_value()
    provider.priority = payload.priority
    provider.enabled = payload.enabled
    await session.flush()
    return provider_response(provider)


@router.delete("/{provider_id}", status_code=204)
async def remove_metadata_provider(provider_id: UUID, _: Admin, session: Session) -> None:
    provider = await session.get(MetadataProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404)
    await session.delete(provider)
