"""Administrator-only account state: who exists, and who is still allowed in.

WHY THIS EXISTS. `get_current_user` has always refused a session whose account
has `is_active = false`, and destroyed the session while it was at it — the
enforcement half was complete and proven. There was no way to reach it: the API
carried no user route of any kind, so the only way to shut an account out of a
running instance was an UPDATE against the database by hand. A household product
that can invite somebody must be able to un-invite them.

REVOCATION IS IMMEDIATE, NOT EVENTUAL. Deactivating an account drops every one
of its sessions from Redis in the same request. Leaving them to expire would
give a removed member up to seven more days of a library they are no longer part
of, and `logout-everywhere` is the account's own call, not an administrator's.
API keys need no separate step: `authenticate_api_key` refuses an inactive
owner.

ONE GUARD, AND WHY IT IS THE WHOLE OF IT. An administrator cannot deactivate
their own account. That single refusal is also what keeps the last administrator:
the caller has passed `require_role(ADMIN)` and `get_current_user`, so the caller
is by definition an *active* administrator, and therefore no other administrator
is ever the last active one. A separate "last administrator" refusal would be a
branch no request can reach. What that argument does not cover is two
administrators deactivating each other at the same instant, and a check of this
shape would not cover it either -- under READ COMMITTED both transactions read
the other as still active. That is recorded, unproven, in
`.gauntlet/pieces/02-auth/HOLES.md` rather than answered with a guard that looks
like it handles it.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role, revoke_all_sessions
from pornarr_db.audit import write_audit
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/users", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class SelfDeactivationError(PornarrError):
    code = "USER_CANNOT_DEACTIVATE_SELF"
    status = 409


class UserResponse(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    role: UserRole
    is_active: bool


class UserStateUpdate(BaseModel):
    is_active: bool


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(_: Admin, session: Session) -> list[UserResponse]:
    rows = await session.scalars(select(User).order_by(User.username))
    return [user_response(user) for user in rows]


@router.patch("/{user_id}", response_model=UserResponse)
async def set_user_state(
    user_id: UUID,
    payload: UserStateUpdate,
    admin: Admin,
    session: Session,
    request: Request,
) -> UserResponse:
    """Deactivate or restore an account, ending its sessions when it is shut out."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)

    if not payload.is_active and user.id == admin.id:
        raise SelfDeactivationError("An administrator cannot deactivate their own account.")

    if user.is_active != payload.is_active:
        user.is_active = payload.is_active
        write_audit(
            session,
            actor_id=admin.id,
            action="user.activated" if payload.is_active else "user.deactivated",
            target=str(user.id),
            context={"username": user.username},
        )
        if not payload.is_active:
            # Before the response, not at the next expiry: a held cookie is a
            # working session for as long as its Redis record lives.
            await revoke_all_sessions(request, user.id)
    await session.flush()
    return user_response(user)
