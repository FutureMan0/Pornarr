"""Local-account authentication endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    InvalidCredentialsError,
    LoginRateLimitedError,
    authenticate_user,
    clear_login_failures,
    create_session,
    database_session,
    get_current_user,
    is_login_rate_limited,
    record_failed_login,
    revoke_all_sessions,
    revoke_session,
)
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
AUTHENTICATION_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}


class LoginRequest(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: SecretStr


class CurrentUserResponse(BaseModel):
    id: str
    username: str
    role: UserRole


def current_user_response(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(id=str(user.id), username=user.username, role=user.role)


def _cookie_is_secure(request: Request) -> bool:
    """`SESSION_COOKIE_SECURE`, which defaults to true.

    It used to be `app_env == "production"`, and `.env.example` -- the file
    `make setup` writes -- ships `APP_ENV=development`, so the documented
    installation served its session cookie without `Secure` behind the TLS proxy
    installation.md tells operators to put in front of it. The default is now the
    safe one and the opt-out is the explicit local case; `Settings` refuses the
    opt-out under `APP_ENV=production`.
    """
    return request.app.state.settings.session_cookie_secure


def set_auth_cookies(response: Response, request: Request, session: str, csrf: str) -> None:
    secure = _cookie_is_secure(request)
    response.set_cookie(
        SESSION_COOKIE,
        session,
        max_age=SESSION_TTL_SECONDS,
        path="/api",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=SESSION_TTL_SECONDS,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def clear_auth_cookies(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/api", secure=_cookie_is_secure(request), samesite="lax"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=_cookie_is_secure(request), samesite="lax")


@router.post(
    "/login",
    response_model=CurrentUserResponse,
    responses={
        **AUTHENTICATION_ERRORS,
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> CurrentUserResponse:
    username = credentials.username
    if await is_login_rate_limited(request, username):
        raise LoginRateLimitedError("Too many login attempts.")

    user = await authenticate_user(session, username, credentials.password.get_secret_value())
    if user is None:
        if await record_failed_login(request, username):
            raise LoginRateLimitedError("Too many login attempts.")
        raise InvalidCredentialsError("Invalid username or password.")

    await clear_login_failures(request, username)
    session_token, csrf_token = await create_session(request, user)
    set_auth_cookies(response, request, session_token, csrf_token)
    return current_user_response(user)


@router.post("/logout", status_code=204, responses=AUTHENTICATION_ERRORS)
async def logout(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token is not None:
        await revoke_session(request, session_token, user.id)
    clear_auth_cookies(response, request)


@router.get("/me", response_model=CurrentUserResponse, responses={401: {"model": ErrorResponse}})
async def current_user(user: Annotated[User, Depends(get_current_user)]) -> CurrentUserResponse:
    return current_user_response(user)


@router.post("/logout-everywhere", status_code=204, responses=AUTHENTICATION_ERRORS)
async def logout_everywhere(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await revoke_all_sessions(request, user.id)
    clear_auth_cookies(response, request)
