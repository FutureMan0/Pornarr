"""Session authentication and authorization dependencies."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Network, IPv6Network, ip_address
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
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
PASSWORDLESS_PASSWORD_HASH = "!"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_password_hasher = PasswordHasher()

# A real Argon2id hash of a value nobody holds, verified against when the name
# offered at sign-in has no account or has no local password. See
# `authenticate_user` for why the work has to happen anyway. It cannot be
# `PASSWORDLESS_PASSWORD_HASH`: "!" is not a valid encoded hash, so verifying
# against it raises `InvalidHashError` before any key derivation runs and costs
# nothing -- which is the leak, not the fix.
_ABSENT_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(32))


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
    except (InvalidHashError, VerificationError):
        return False


def has_local_password(user: User) -> bool:
    return user.password_hash != PASSWORDLESS_PASSWORD_HASH


def session_key(token: str) -> str:
    return f"pornarr:auth:session:{token}"


def user_sessions_key(user_id: UUID) -> str:
    return f"pornarr:auth:user-sessions:{user_id}"


def _address_is_trusted(address: str, networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def client_address(request: Request) -> str:
    """The address the caller really came from, as the rate limiter counts it.

    `request.client.host` is the peer, and behind the reverse proxy
    docs/operations/deployment.md documents the peer is the proxy for every user
    of the instance -- so six wrong guesses from one anonymous caller filled the
    one bucket everybody shares and refused every account's sign-in for fifteen
    minutes.

    `X-Forwarded-For` names the real client, but believing it unconditionally is
    worse than not reading it at all: any caller can write it, so an attacker
    would put every guess in a bucket of its own and the limit would stop
    existing. It is therefore read only when the peer is one of TRUSTED_PROXIES,
    and then from the right, skipping hops that are themselves trusted. The
    first address a trusted hop did not add is the furthest one that cannot have
    been forged.
    """
    peer = request.client.host if request.client is not None else "unknown"
    networks = request.app.state.settings.trusted_proxy_networks
    if not networks or not _address_is_trusted(peer, networks):
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    for candidate in reversed([part.strip() for part in forwarded.split(",")]):
        if candidate and not _address_is_trusted(candidate, networks):
            return candidate
    return peer


def _rate_limit_keys(request: Request, username: str) -> tuple[str, str]:
    address_digest = hashlib.sha256(client_address(request).encode()).hexdigest()
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


@asynccontextmanager
async def streaming_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A session the caller closes itself, for a route that returns a stream.

    `database_session` is a yield dependency, and FastAPI holds those open until
    the response *body* has finished. A `StreamingResponse` body does not finish
    while the client stays connected, so a streaming route that depends on it
    pins one pooled connection -- inside an open transaction -- for the whole
    life of the stream. The pool is 5 plus 10 overflow, so fifteen open event
    streams exhaust it and every route starts answering 500. Streaming routes
    take their session from here and give it back before returning the response.
    """
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
    """Check a username and password, in the same time whatever the answer.

    The verification runs even when there is no such account. Skipping it
    answered an unknown name in the time of one indexed SELECT and a known one
    in the time of a full Argon2id verification -- measured at 9ms against 56ms
    -- which let an anonymous caller enumerate every username on the instance
    with a stopwatch and no successful sign-in. An account with no local
    password (OIDC-only, `PASSWORDLESS_PASSWORD_HASH`) is verified against the
    same dummy for the same reason, so it is not distinguishable either.
    """
    user = await session.scalar(select(User).where(User.username == username))
    stored = (
        user.password_hash
        if user is not None and has_local_password(user)
        else _ABSENT_PASSWORD_HASH
    )
    verified = verify_password(stored, password)
    if user is None or not user.is_active or not verified:
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


async def get_streaming_user(request: Request) -> User:
    """`get_current_user` for a route that returns a stream, holding nothing after it returns."""
    async with streaming_session(request) as session:
        return await get_current_user(request, session)


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


def is_invite_redemption(path: str) -> bool:
    """`/api/invites/<token>/redeem`, and nothing else under that prefix.

    Redemption is exempt for the same reason login is: there is no session to
    protect. The caller has no account yet, and an attacker who could make a
    victim's browser redeem a token is an attacker who already holds the token
    and could simply redeem it themselves.

    Matched on shape rather than by prefix, so a future POST under /api/invites
    does not inherit the exemption by accident.
    """
    parts = path.split("/")
    return len(parts) == 5 and parts[:3] == ["", "api", "invites"] and parts[4] == "redeem"


def _is_machine_request(request: Request) -> bool:
    """An API-key request that carries no ambient credential.

    api-contract.md L62-63 exempts API-key requests for one stated reason -- they
    "carry no ambient credential" -- so the exemption is only earned by a request
    that has none. Keying it on the header alone let any caller opt out of the
    check by naming a header the product never looked at: a session cookie plus
    `X-Api-Key: anything` reached every unsafe route without a token, and the
    request was then authorised by the cookie, because `get_current_user` falls
    back to the session when the key authenticates nothing.
    """
    return request.headers.get("X-Api-Key") is not None and SESSION_COOKIE not in request.cookies


async def enforce_csrf(request: Request) -> None:
    """Require a session-bound double-submit token on every unsafe API request."""
    if (
        request.method not in _UNSAFE_METHODS
        or request.url.path
        in {
            "/api/auth/login",
            "/api/setup/validate-library-path",
            "/api/setup/complete",
            "/api/setup/test-indexer",
            "/api/setup/test-download-client",
        }
        or is_invite_redemption(request.url.path)
        or _is_machine_request(request)
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
