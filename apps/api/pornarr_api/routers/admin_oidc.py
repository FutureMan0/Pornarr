"""Administrator-only OIDC provider configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_api.oidc import OidcDiscoveryError, discover, normalise_issuer
from pornarr_db.audit import write_audit
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
    username_claim: Annotated[str, Field(min_length=1, max_length=128)] = "preferred_username"
    role_claim: Annotated[str, Field(min_length=1, max_length=128)] = "groups"
    role_mapping: dict[str, UserRole] = Field(default_factory=dict)
    default_role: UserRole = UserRole.USER
    required_claim: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    required_claim_value: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    enabled: bool = True

    @field_validator("issuer")
    @classmethod
    def _issuer_is_safe(cls, value: str) -> str:
        try:
            return normalise_issuer(value)
        except OidcDiscoveryError as exc:
            raise ValueError(exc.message) from exc

    @model_validator(mode="after")
    def restriction_is_complete(self) -> Self:
        if (self.required_claim is None) != (self.required_claim_value is None):
            raise ValueError("required_claim and required_claim_value must be set together")
        return self


class ProviderResponse(BaseModel):
    id: UUID
    name: str
    issuer: str
    client_id: str
    scopes: list[str]
    username_claim: str
    role_claim: str
    role_mapping: dict[str, UserRole]
    default_role: UserRole
    required_claim: str | None
    required_claim_value: str | None
    enabled: bool
    discovery_fetched_at: datetime | None


def provider_response(provider: OidcProvider) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        issuer=provider.issuer,
        client_id=provider.client_id,
        scopes=provider.scopes,
        username_claim=provider.username_claim,
        role_claim=provider.role_claim,
        role_mapping={key: UserRole(value) for key, value in provider.role_mapping.items()},
        default_role=UserRole(provider.default_role),
        required_claim=provider.required_claim,
        required_claim_value=provider.required_claim_value,
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
async def create_provider(
    payload: ProviderWrite, user: Admin, session: Session
) -> ProviderResponse:
    provider = OidcProvider(
        name=payload.name,
        issuer=payload.issuer,
        client_id=payload.client_id,
        client_secret=payload.client_secret.get_secret_value(),
        scopes=payload.scopes,
        username_claim=payload.username_claim,
        role_claim=payload.role_claim,
        role_mapping={key: value.value for key, value in payload.role_mapping.items()},
        default_role=payload.default_role.value,
        required_claim=payload.required_claim,
        required_claim_value=payload.required_claim_value,
        enabled=payload.enabled,
    )
    session.add(provider)
    await session.flush()
    write_audit(session, actor_id=user.id, action="oidc_provider.created", target=str(provider.id))
    return provider_response(provider)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: UUID, user: Admin, session: Session) -> None:
    await session.delete(await provider_or_404(session, provider_id))
    write_audit(session, actor_id=user.id, action="oidc_provider.deleted", target=str(provider_id))


@router.post("/{provider_id}/test", response_model=ProviderResponse)
async def test_provider(
    provider_id: UUID, request: Request, user: Admin, session: Session
) -> ProviderResponse:
    provider = await provider_or_404(session, provider_id)
    provider.discovery_document = await discover(
        provider.issuer,
        allow_private_issuers=request.app.state.settings.oidc_allow_private_issuers,
    )
    provider.discovery_fetched_at = datetime.now(UTC)
    write_audit(session, actor_id=user.id, action="oidc_provider.tested", target=str(provider.id))
    return provider_response(provider)
