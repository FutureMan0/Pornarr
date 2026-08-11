"""Candidate generation backed by stored profiles and pure scoring."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.scoring import RecommendationCandidate as ScoringCandidate
from pornarr_core.scoring import RecommendationWeights, UserInterestProfile, score_recommendation
from pornarr_db.models.entities import MediaPerformer, MediaTag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent
from pornarr_db.models.preferences import PreferenceAxis, UserPreference
from pornarr_db.models.recommendation import RecommendationCandidate

MODEL_VERSION = "v1"
MAX_CANDIDATES = 20
EXPLORATION_SLOTS = 4


async def generate_recommendations(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime | None = None,
    weights: RecommendationWeights | None = None,
) -> list[RecommendationCandidate]:
    """Replace one user's unexpired candidates with a diverse scored selection."""

    generated_at = now or datetime.now(UTC)
    profile = await _profile(session, user_id)
    seen = set(
        await session.scalars(
            select(UserEvent.media_id).where(
                UserEvent.user_id == user_id, UserEvent.media_id.is_not(None)
            )
        )
    )
    rows = await session.execute(
        select(Media, MediaFile)
        .join(MediaFile, MediaFile.media_id == Media.id)
        .where(MediaFile.is_active.is_(True))
    )
    scored = []
    for media, media_file in rows:
        if media.id in seen:
            continue
        tags = frozenset(
            str(value)
            for value in await session.scalars(
                select(MediaTag.tag_id).where(MediaTag.media_id == media.id)
            )
        )
        performers = frozenset(
            str(value)
            for value in await session.scalars(
                select(MediaPerformer.performer_id).where(MediaPerformer.media_id == media.id)
            )
        )
        result = score_recommendation(
            ScoringCandidate(
                tags=tags,
                performers=performers,
                studio=media.studio.casefold() if media.studio else None,
                quality=media_file.quality,
                release_date=media.release_date,
            ),
            profile,
            weights=weights,
            now=generated_at.date(),
        )
        if not result.blocked:
            scored.append((media, tags, performers, result))
    selected = _diverse(scored)
    await session.execute(
        delete(RecommendationCandidate).where(RecommendationCandidate.user_id == user_id)
    )
    records = [
        RecommendationCandidate(
            user_id=user_id,
            media_id=media.id,
            score=result.score,
            reason_json=_reason(
                tags=tags,
                performers=performers,
                studio=media.studio,
                breakdown=result.breakdown,
            ),
            model_version=MODEL_VERSION,
            expires_at=generated_at + timedelta(days=1),
        )
        for media, tags, performers, result in selected
    ]
    session.add_all(records)
    await session.flush()
    return records


async def _profile(session: AsyncSession, user_id: UUID) -> UserInterestProfile:
    values = list(
        await session.scalars(select(UserPreference).where(UserPreference.user_id == user_id))
    )
    by_axis: dict[str, dict[str, float]] = {axis.value: {} for axis in PreferenceAxis}
    blocks: dict[str, set[str]] = {
        PreferenceAxis.TAG.value: set(),
        PreferenceAxis.PERFORMER.value: set(),
    }
    for value in values:
        by_axis[value.axis][value.subject] = value.score
        if value.raw_score <= -100:
            blocks.setdefault(value.axis, set()).add(value.subject)
    return UserInterestProfile(
        tags=by_axis["tag"],
        performers=by_axis["performer"],
        studios=by_axis["studio"],
        qualities=by_axis["quality"],
        blocked_tags=frozenset(blocks["tag"]),
        blocked_performers=frozenset(blocks["performer"]),
    )


def _diverse(scored):
    selected = []
    performer_counts: Counter[str] = Counter()
    studio_counts: Counter[str] = Counter()
    exploration = [
        value
        for value in scored
        if sum(value[3].breakdown[key] for key in ("tag", "performer", "studio", "quality")) == 0
    ]
    _append_diverse(exploration, EXPLORATION_SLOTS, selected, performer_counts, studio_counts)
    _append_diverse(scored, MAX_CANDIDATES, selected, performer_counts, studio_counts)
    return selected


def _append_diverse(scored, limit, selected, performer_counts, studio_counts) -> None:
    selected_media_ids = {media.id for media, *_ in selected}
    for media, tags, performers, result in sorted(
        scored, key=lambda value: (-value[3].score, str(value[0].id))
    ):
        if len(selected) == limit or media.id in selected_media_ids:
            continue
        if any(performer_counts[item] >= 2 for item in performers):
            continue
        studio = media.studio.casefold() if media.studio else None
        if studio is not None and studio_counts[studio] >= 3:
            continue
        selected.append((media, tags, performers, result))
        selected_media_ids.add(media.id)
        performer_counts.update(performers)
        if studio is not None:
            studio_counts[studio] += 1


def _reason(*, tags, performers, studio, breakdown):
    dominant = max(breakdown, key=breakdown.get)
    return {
        "matched_tags": list(tags),
        "matched_performers": sorted(performers),
        "matched_studios": [studio] if studio else [],
        "dominant_factor": dominant,
        "score_breakdown": breakdown,
    }
