"""The name you appear under, separate from the name you sign in with.

The first-run screen for joining a friend's server asks for a "display name —
how you show up in the library". This is where it is set and changed. Clearing
it falls back to the username rather than leaving an author blank.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.authorship import author_name
from pornarr_api.errors import ErrorResponse
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/account/profile", tags=["account"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class ProfileWrite(BaseModel):
    # Explicitly nullable: sending null is how you go back to the username, and
    # a missing key would be indistinguishable from that if this were optional.
    display_name: Annotated[str | None, Field(max_length=64)]

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str | None) -> str | None:
        stripped = (value or "").strip()
        return stripped or None


class ProfileResponse(BaseModel):
    username: str
    display_name: str | None
    author_name: str
    role: UserRole


def profile_response(user: User) -> ProfileResponse:
    return ProfileResponse(
        username=user.username,
        display_name=user.display_name,
        author_name=author_name(user),
        role=user.role,
    )


@router.get("", response_model=ProfileResponse)
async def read_profile(user: CurrentUser) -> ProfileResponse:
    return profile_response(user)


@router.patch("", response_model=ProfileResponse, responses={422: {"model": ErrorResponse}})
async def write_profile(
    payload: ProfileWrite, user: CurrentUser, session: Session
) -> ProfileResponse:
    user.display_name = payload.display_name
    session.add(user)
    await session.flush()
    return profile_response(user)
