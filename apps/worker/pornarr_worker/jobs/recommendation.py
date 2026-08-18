"""Nightly recommendation-candidate generation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from pornarr_core.scoring import RecommendationWeights
from pornarr_db.models.user import User
from pornarr_db.recommendations import RecommendationOptions, generate_recommendations
from pornarr_db.session import session_scope
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.config import get_settings
from pornarr_shared.jobs import job


async def refresh_recommendations_job(_: dict[str, Any]) -> int:
    """Refresh every user's scored, expiring recommendation list."""

    defaults = get_settings()
    async with session_scope() as session:
        settings = await get_runtime_settings(session, defaults)
        weights = RecommendationWeights(
            tag=settings.recommendation_tag_weight,
            performer=settings.recommendation_performer_weight,
            studio=settings.recommendation_studio_weight,
            quality=settings.recommendation_quality_weight,
            recency=settings.recommendation_recency_weight,
            popularity=settings.recommendation_popularity_weight,
        )
        options = RecommendationOptions(
            use_ratings=settings.recommendation_use_ratings,
            include_friend_picks=settings.recommendation_include_friend_picks,
            hide_finished=settings.recommendation_hide_finished,
            include_shorts=settings.recommendation_include_shorts,
        )
        user_ids = list(await session.scalars(select(User.id)))
        for user_id in user_ids:
            await generate_recommendations(session, user_id, weights=weights, options=options)
        return len(user_ids)


REFRESH_RECOMMENDATIONS_JOB = job(refresh_recommendations_job)
