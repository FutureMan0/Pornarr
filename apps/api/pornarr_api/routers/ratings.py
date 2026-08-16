"""Five-star ratings, as shown on the detail screen and the A6 overview.

A rating is per person and per title, so setting one twice is an update rather
than a second row. The summary carries the full star breakdown because the
design draws a bar per star, and deriving that client-side would mean shipping
every rating to every viewer.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_api.idempotency import insert_once
from pornarr_db.models.media import Media
from pornarr_db.models.social import MAXIMUM_STARS, MINIMUM_STARS, Rating
from pornarr_db.models.user import User

router = APIRouter(prefix="/media", tags=["ratings"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]

Stars = Annotated[int, Field(ge=MINIMUM_STARS, le=MAXIMUM_STARS)]


class RatingWrite(BaseModel):
    stars: Stars


class RatingSummaryResponse(BaseModel):
    media_id: UUID
    average: float | None
    count: int
    # Keyed by star value as a string: JSON object keys are strings, and an
    # integer-keyed map would round-trip inconsistently across clients.
    breakdown: dict[str, int]
    your_stars: int | None


async def media_or_404(session: AsyncSession, media_id: UUID) -> Media:
    media = await session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    return media


async def rating_summary(
    session: AsyncSession, media_id: UUID, user_id: UUID
) -> RatingSummaryResponse:
    rows = list(
        await session.execute(
            select(Rating.stars, func.count())
            .where(Rating.media_id == media_id)
            .group_by(Rating.stars)
        )
    )
    breakdown = {str(star): 0 for star in range(MINIMUM_STARS, MAXIMUM_STARS + 1)}
    total = 0
    weighted = 0
    for stars, count in rows:
        breakdown[str(stars)] = count
        total += count
        weighted += stars * count
    your_stars = await session.scalar(
        select(Rating.stars).where(Rating.media_id == media_id, Rating.user_id == user_id)
    )
    return RatingSummaryResponse(
        media_id=media_id,
        average=round(weighted / total, 2) if total else None,
        count=total,
        breakdown=breakdown,
        your_stars=your_stars,
    )


@router.get(
    "/{media_id}/rating",
    response_model=RatingSummaryResponse,
    responses={404: {"model": ErrorResponse}},
)
async def read_rating(media_id: UUID, user: CurrentUser, session: Session) -> RatingSummaryResponse:
    await media_or_404(session, media_id)
    return await rating_summary(session, media_id, user.id)


@router.put(
    "/{media_id}/rating",
    response_model=RatingSummaryResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def set_rating(
    media_id: UUID, payload: RatingWrite, user: CurrentUser, session: Session
) -> RatingSummaryResponse:
    """Set-or-replace, so re-rating from a second tab cannot collide."""

    await media_or_404(session, media_id)
    rating = await session.scalar(
        select(Rating).where(Rating.media_id == media_id, Rating.user_id == user.id)
    )
    if rating is None and not await insert_once(
        session, Rating(user_id=user.id, media_id=media_id, stars=payload.stars)
    ):
        # Another tab inserted between the read and the write; fall through to
        # the update rather than failing a click the user already made.
        rating = await session.scalar(
            select(Rating).where(Rating.media_id == media_id, Rating.user_id == user.id)
        )
    if rating is not None:
        rating.stars = payload.stars
    await session.flush()
    return await rating_summary(session, media_id, user.id)


@router.delete(
    "/{media_id}/rating",
    response_model=RatingSummaryResponse,
    responses={404: {"model": ErrorResponse}},
)
async def clear_rating(
    media_id: UUID, user: CurrentUser, session: Session
) -> RatingSummaryResponse:
    """Idempotent: withdrawing a rating you never gave is not an error."""

    await media_or_404(session, media_id)
    rating = await session.scalar(
        select(Rating).where(Rating.media_id == media_id, Rating.user_id == user.id)
    )
    if rating is not None:
        await session.delete(rating)
        await session.flush()
    return await rating_summary(session, media_id, user.id)
