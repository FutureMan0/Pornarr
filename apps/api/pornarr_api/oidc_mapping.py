"""Map verified OIDC claims to local users and roles."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import PASSWORDLESS_PASSWORD_HASH, has_local_password
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError


class OidcIdentityNotAllowedError(PornarrError):
    code = "OIDC_IDENTITY_NOT_ALLOWED"
    status = 403


class OidcUsernameConflictError(PornarrError):
    code = "OIDC_USERNAME_CONFLICT"
    status = 409


class OidcIdentityAlreadyLinkedError(PornarrError):
    code = "OIDC_IDENTITY_ALREADY_LINKED"
    status = 409


def _claim_values(claims: dict[str, Any], name: str) -> list[str]:
    value = claims.get(name)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _role(provider: OidcProvider, claims: dict[str, Any]) -> UserRole:
    mapping = provider.role_mapping
    mapped = [
        mapping[value] for value in _claim_values(claims, provider.role_claim) if value in mapping
    ]
    if UserRole.ADMIN.value in mapped:
        return UserRole.ADMIN
    if UserRole.USER.value in mapped:
        return UserRole.USER
    return UserRole(provider.default_role)


def _is_allowed(provider: OidcProvider, claims: dict[str, Any]) -> bool:
    if provider.required_claim is None:
        return True
    return provider.required_claim_value in _claim_values(claims, provider.required_claim)


def _subject(claims: dict[str, Any]) -> str:
    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise OidcIdentityNotAllowedError("The OIDC identity has no usable subject.")
    return subject


async def link_oidc_identity(
    session: AsyncSession, provider: OidcProvider, claims: dict[str, Any], user: User
) -> None:
    if not _is_allowed(provider, claims):
        raise OidcIdentityNotAllowedError(
            "This OIDC identity is not allowed by the provider policy."
        )
    subject = _subject(claims)
    identity = await session.scalar(
        select(OidcIdentity).where(
            OidcIdentity.provider_id == provider.id, OidcIdentity.subject == subject
        )
    )
    if identity is not None:
        if identity.user_id != user.id:
            raise OidcIdentityAlreadyLinkedError(
                "This OIDC identity is already linked to another account."
            )
        return
    session.add(OidcIdentity(provider_id=provider.id, user_id=user.id, subject=subject))


async def _verified_email_user(session: AsyncSession, claims: dict[str, Any]) -> User | None:
    email = claims.get("email")
    if claims.get("email_verified") is not True or not isinstance(email, str):
        return None
    user = await session.scalar(select(User).where(User.username == email))
    if user is None or not user.is_active or not has_local_password(user):
        return None
    return user


async def resolve_oidc_user(
    session: AsyncSession, provider: OidcProvider, claims: dict[str, Any]
) -> User:
    if not _is_allowed(provider, claims):
        raise OidcIdentityNotAllowedError(
            "This OIDC identity is not allowed by the provider policy."
        )
    subject = _subject(claims)
    role = _role(provider, claims)
    identity = await session.scalar(
        select(OidcIdentity).where(
            OidcIdentity.provider_id == provider.id, OidcIdentity.subject == subject
        )
    )
    if identity is not None:
        user = await session.get(User, identity.user_id)
        if user is None or not user.is_active:
            raise OidcIdentityNotAllowedError(
                "The OIDC identity is not linked to an active account."
            )
        user.role = role
        return user
    user = await _verified_email_user(session, claims)
    if user is not None:
        await link_oidc_identity(session, provider, claims, user)
        user.role = role
        return user
    usernames = _claim_values(claims, provider.username_claim)
    if not usernames or len(usernames[0]) > 64:
        raise OidcIdentityNotAllowedError("The OIDC identity has no usable username.")
    username = usernames[0]
    if await session.scalar(select(User.id).where(User.username == username)) is not None:
        raise OidcUsernameConflictError("The OIDC username belongs to an existing local account.")
    user = User(username=username, password_hash=PASSWORDLESS_PASSWORD_HASH, role=role)
    session.add(user)
    await session.flush()
    session.add(OidcIdentity(provider_id=provider.id, user_id=user.id, subject=subject))
    return user
