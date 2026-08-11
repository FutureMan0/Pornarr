"""Private recommendation delivery and immediate feedback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_core.scoring import RecommendationWeights
from pornarr_db.events import record_user_event
from pornarr_db.models.entities import Performer, Tag
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
    score: float
    reason: dict[str, object]
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


def recommendation_response(candidate: RecommendationCandidate) -> RecommendationResponse:
    return RecommendationResponse(
        media_id=candidate.media_id,
        score=candidate.score,
        reason=candidate.reason_json,
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
    return [recommendation_response(candidate) for candidate in candidates]


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
