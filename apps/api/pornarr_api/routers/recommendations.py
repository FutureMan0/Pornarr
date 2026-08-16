"""Private recommendation delivery and immediate feedback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse
from pornarr_api.routers.sends import SendCreate, SendResponse, list_received, send_media
from pornarr_core.scoring import RecommendationWeights
from pornarr_db.events import record_user_event
from pornarr_db.models.entities import Performer, Tag
from pornarr_db.models.media import Media
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.preferences import UserPreference, UserPreferenceState
from pornarr_db.models.recommendation import RecommendationCandidate
from pornarr_db.models.user import User
from pornarr_db.preferences import refresh_user_interest_profile
from pornarr_db.recommendations import generate_recommendations
from pornarr_db.settings import RuntimeSettings, get_runtime_settings

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class RecommendationResponse(BaseModel):
    media_id: UUID
    title: str
    score: float
    # The same number as `score`, expressed the way the feed prints it: the
    # design shows "94% match", and rounding in the client would drift between
    # the web app and anything else that reads this.
    match_score: int
    reason: dict[str, object]
    # The card lists its reasons as sentences. `reason` keeps the raw signal
    # weights the recommender produced; `reasons` is what a person reads.
    reasons: list[str]
    model_version: str
    expires_at: datetime


class RecommendationFeedbackWrite(BaseModel):
    event_type: UserEventType
    subject_id: UUID | None = None

    @model_validator(mode="after")
    def has_valid_target(self) -> RecommendationFeedbackWrite:
        if self.event_type is UserEventType.NOT_INTERESTED and self.subject_id is None:
            return self
        if self.event_type in {UserEventType.HIDE_TAG, UserEventType.HIDE_PERFORMER}:
            if self.subject_id is not None:
                return self
            raise ValueError("subject_id is required when hiding a tag or performer")
        raise ValueError("event type must be not_interested, hide_tag, or hide_performer")


# How a raw signal name reads on a card. Anything not listed falls back to the
# key itself rather than being dropped, so a new signal shows up as soon as the
# recommender produces it instead of waiting for this table to be updated.
_REASON_LABELS = {
    "tag": "Tags you keep watching",
    "performer": "A performer you follow",
    "studio": "A studio you finish",
    "quality": "Matches the quality you prefer",
    "recency": "New to the library",
    "popularity": "Watched across the server",
    "rating": "Rated highly here",
}


def reason_sentences(reason: dict[str, object]) -> list[str]:
    """Turn the recommender's signal weights into what the card prints.

    Strongest first, and only signals that actually contributed: a reason with
    zero weight explains nothing and would just make every card look the same.
    """
    weighted = [
        (key, float(value))
        for key, value in reason.items()
        if isinstance(value, (int, float)) and float(value) > 0
    ]
    return [_REASON_LABELS.get(key, key) for key, _ in sorted(weighted, key=lambda item: -item[1])]


def recommendation_response(
    candidate: RecommendationCandidate, title: str
) -> RecommendationResponse:
    return RecommendationResponse(
        media_id=candidate.media_id,
        title=title,
        score=candidate.score,
        match_score=max(0, min(100, round(candidate.score * 100))),
        reason=candidate.reason_json,
        reasons=reason_sentences(candidate.reason_json),
        model_version=candidate.model_version,
        expires_at=candidate.expires_at,
    )


def recommendation_weights(settings: RuntimeSettings) -> RecommendationWeights:
    return RecommendationWeights(
        tag=settings.recommendation_tag_weight,
        performer=settings.recommendation_performer_weight,
        studio=settings.recommendation_studio_weight,
        quality=settings.recommendation_quality_weight,
        recency=settings.recommendation_recency_weight,
        popularity=settings.recommendation_popularity_weight,
    )


@router.get("", response_model=list[RecommendationResponse])
async def list_recommendations(user: CurrentUser, session: Session) -> list[RecommendationResponse]:
    candidates = await session.scalars(
        select(RecommendationCandidate)
        .where(
            RecommendationCandidate.user_id == user.id,
            RecommendationCandidate.expires_at > datetime.now(UTC),
        )
        .order_by(RecommendationCandidate.score.desc(), RecommendationCandidate.id)
    )
    items = list(candidates)
    titles = {
        media.id: media.title
        for media in await session.scalars(
            select(Media).where(Media.id.in_([item.media_id for item in items]))
        )
    }
    return [recommendation_response(candidate, titles[candidate.media_id]) for candidate in items]


@router.post("/{media_id}/feedback", status_code=204)
async def record_recommendation_feedback(
    media_id: UUID,
    payload: RecommendationFeedbackWrite,
    request: Request,
    user: CurrentUser,
    session: Session,
) -> None:
    candidate = await session.scalar(
        select(RecommendationCandidate).where(
            RecommendationCandidate.user_id == user.id,
            RecommendationCandidate.media_id == media_id,
            RecommendationCandidate.expires_at > datetime.now(UTC),
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404)
    if (
        payload.event_type is UserEventType.HIDE_TAG
        and await session.get(Tag, payload.subject_id) is None
    ):
        raise HTTPException(status_code=404)
    if (
        payload.event_type is UserEventType.HIDE_PERFORMER
        and await session.get(Performer, payload.subject_id) is None
    ):
        raise HTTPException(status_code=404)

    await record_user_event(
        session,
        user.id,
        payload.event_type,
        media_id=media_id,
        subject_id=payload.subject_id,
    )
    await session.flush()
    await refresh_user_interest_profile(session, user.id)
    settings = await get_runtime_settings(session, request.app.state.settings)
    await generate_recommendations(session, user.id, weights=recommendation_weights(settings))


@router.delete("/profile", status_code=204)
async def reset_recommendation_profile(user: CurrentUser, session: Session) -> None:
    """Remove the user's signals so the next refresh starts from an empty profile."""

    for model in (RecommendationCandidate, UserPreference, UserPreferenceState, UserEvent):
        await session.execute(delete(model).where(model.user_id == user.id))


# The design puts "Recommend to a friend" and "Sent to you" inside the feed, so
# the feed is where they are addressed from. The storage and the rules live in
# `sends.py`; these are the same operations under the path the screens use, not
# a second implementation.


@router.post(
    "/send",
    response_model=SendResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def send_recommendation(
    payload: SendCreate, user: CurrentUser, session: Session
) -> SendResponse:
    """Hand a title to someone with a note. They are told who sent it."""

    return await send_media(payload, user, session)


@router.get("/sent-to-me", response_model=list[SendResponse])
async def sent_to_me(
    user: CurrentUser,
    session: Session,
    unseen_only: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[SendResponse]:
    """The one place in the product where another person is named.

    Everything else is anonymous under `anonymous_social`; a hand-picked
    recommendation is worthless without knowing whose taste it was.
    """
    return await list_received(user, session, unseen_only, limit, offset)
