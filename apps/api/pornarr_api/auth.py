"""Session authentication and authorization dependencies."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.audit import write_audit
from pornarr_db.models.api_keys import UserApiKey
from pornarr_db.models.user import User, UserRole
from pornarr_shared.audit import AuditSource
from pornarr_shared.errors import PornarrError

SESSION_COOKIE = "pornarr_session"
CSRF_COOKIE = "pornarr_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
API_KEY_PREFIX_LENGTH = 12
LOGIN_ATTEMPT_LIMIT = 6
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_password_hasher = PasswordHasher()


class InvalidCredentialsError(PornarrError):
    code = "INVALID_CREDENTIALS"
    status = 401


class NotAuthenticatedError(PornarrError):
    code = "NOT_AUTHENTICATED"
    status = 401


class ForbiddenError(PornarrError):
    code = "FORBIDDEN"
    status = 403


class CsrfError(PornarrError):
    code = "CSRF_FAILED"
    status = 403


class LoginRateLimitedError(PornarrError):
    code = "LOGIN_RATE_LIMITED"
    status = 429


@dataclass(frozen=True)
class SessionRecord:
    user_id: UUID
    csrf_token: str


def hash_password(password: str) -> str:
    """Create an Argon2id password hash for a new local account."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerificationError:
        return False


def session_key(token: str) -> str:
    return f"pornarr:auth:session:{token}"


def user_sessions_key(user_id: UUID) -> str:
    return f"pornarr:auth:user-sessions:{user_id}"


def _rate_limit_keys(request: Request, username: str) -> tuple[str, str]:
    address = request.client.host if request.client is not None else "unknown"
    address_digest = hashlib.sha256(address.encode()).hexdigest()
    username_digest = hashlib.sha256(username.casefold().encode()).hexdigest()
    return (
        f"pornarr:auth:login:ip:{address_digest}",
        f"pornarr:auth:login:account:{username_digest}",
    )


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request, using the API process' one engine."""
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_session(request: Request, user: User) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    record = json.dumps({"user_id": str(user.id), "csrf_token": csrf_token})
    redis = request.app.state.redis
    await redis.set(session_key(token), record, ex=SESSION_TTL_SECONDS)
    await redis.sadd(user_sessions_key(user.id), token)
    await redis.expire(user_sessions_key(user.id), SESSION_TTL_SECONDS)
    return token, csrf_token


async def session_record(request: Request, token: str | None = None) -> SessionRecord | None:
    session_token = token or request.cookies.get(SESSION_COOKIE)
    if not session_token:
        return None

    stored = await request.app.state.redis.get(session_key(session_token))
    if stored is None:
        return None

    try:
        record = json.loads(stored)
        user_id = UUID(record["user_id"])
        csrf_token = record["csrf_token"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(csrf_token, str):
        return None
    return SessionRecord(user_id=user_id, csrf_token=csrf_token)


async def revoke_session(request: Request, token: str, user_id: UUID) -> None:
    redis = request.app.state.redis
    await redis.delete(session_key(token))
    await redis.srem(user_sessions_key(user_id), token)


async def revoke_all_sessions(request: Request, user_id: UUID) -> None:
    redis = request.app.state.redis
    async for token in redis.sscan_iter(user_sessions_key(user_id)):
        await redis.delete(session_key(token))
    await redis.delete(user_sessions_key(user_id))


async def is_login_rate_limited(request: Request, username: str) -> bool:
    redis = request.app.state.redis
    for key in _rate_limit_keys(request, username):
        value = await redis.get(key)
        if value is not None and int(value) >= LOGIN_ATTEMPT_LIMIT:
            return True
    return False


async def record_failed_login(request: Request, username: str) -> bool:
    redis = request.app.state.redis
    attempts = []
    for key in _rate_limit_keys(request, username):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, LOGIN_ATTEMPT_WINDOW_SECONDS)
        attempts.append(count)
    return any(count >= LOGIN_ATTEMPT_LIMIT for count in attempts)


async def clear_login_failures(request: Request, username: str) -> None:
    await request.app.state.redis.delete(*_rate_limit_keys(request, username))


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        return None
    if _password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> User:
    api_key = request.headers.get("X-Api-Key")
    if api_key and not request.url.path.startswith("/api/auth"):
        user = await authenticate_api_key(session, api_key)
        if user is not None:
            request.state.auth_source = AuditSource.API_KEY
            return user
    record = await session_record(request)
    if record is None:
        raise NotAuthenticatedError("A valid session is required.")

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        token = request.cookies.get(SESSION_COOKIE)
        if token is not None:
            await revoke_session(request, token, record.user_id)
        raise NotAuthenticatedError("A valid session is required.")
    return user


def generate_api_key() -> str:
    return f"pnr_{secrets.token_urlsafe(32)}"


async def authenticate_api_key(session: AsyncSession, value: str) -> User | None:
    if not value.startswith("pnr_") or len(value) < API_KEY_PREFIX_LENGTH:
        return None
    key = await session.scalar(
        select(UserApiKey).where(UserApiKey.prefix == value[:API_KEY_PREFIX_LENGTH])
    )
    if key is None or not verify_password(key.key_hash, value):
        return None
    user = await session.get(User, key.user_id)
    if user is None or not user.is_active:
        return None
    key.last_used_at = datetime.now(UTC)
    write_audit(
        session,
        actor_id=user.id,
        source=AuditSource.API_KEY,
        action="apikey.used",
        target=str(key.id),
    )
    return user


def require_role(role: UserRole) -> Callable[..., object]:
    async def dependency(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if getattr(
            request.state, "auth_source", None
        ) is AuditSource.API_KEY and request.url.path.startswith("/api/admin/oidc"):
            raise ForbiddenError("API keys cannot manage authentication settings.")
        if user.role != role:
            raise ForbiddenError("This role is not permitted for the route.")
        return user

    return dependency


async def enforce_csrf(request: Request) -> None:
    """Require a session-bound double-submit token on every unsafe API request."""
    if (
        request.method not in _UNSAFE_METHODS
        or request.url.path == "/api/auth/login"
        or request.headers.get("X-Api-Key")
    ):
        return

    record = await session_record(request)
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if (
        record is None
        or cookie_token is None
        or header_token is None
        or not hmac.compare_digest(cookie_token, header_token)
        or not hmac.compare_digest(record.csrf_token, header_token)
    ):
        raise CsrfError("A valid CSRF token is required.")
