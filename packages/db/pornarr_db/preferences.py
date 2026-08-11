"""Incremental interest-profile computation from opaque user events."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import MediaPerformer, MediaTag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.preferences import (
    PreferenceAxis,
    UserPreference,
    UserPreferenceState,
)
from pornarr_db.models.user import User

HALF_LIFE_DAYS = 90
EVENT_WEIGHTS = {
    UserEventType.REQUEST: 10.0,
    UserEventType.FAVOURITE: 8.0,
    UserEventType.UNFAVOURITE: -8.0,
    UserEventType.COMPLETED: 5.0,
    UserEventType.VIEW: 1.0,
    UserEventType.NOT_INTERESTED: -8.0,
    UserEventType.HIDE_TAG: -100.0,
    UserEventType.HIDE_PERFORMER: -100.0,
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decay_factor(start: datetime, end: datetime) -> float:
    elapsed_days = max((end - _utc(start)).total_seconds(), 0) / 86_400
    return 0.5 ** (elapsed_days / HALF_LIFE_DAYS)


async def refresh_interest_profiles(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Incrementally refresh every user's profile without re-reading old events."""

    user_ids = list(await session.scalars(select(User.id)))
    for user_id in user_ids:
        await refresh_user_interest_profile(session, user_id, now=now)
    return len(user_ids)


async def refresh_user_interest_profile(
    session: AsyncSession, user_id: UUID, *, now: datetime | None = None
) -> list[UserPreference]:
    """Apply unseen events and decay stored raw scores to the supplied instant."""

    computed_at = _utc(now or datetime.now(UTC))
    state = await session.get(UserPreferenceState, user_id)
    if state is None:
        state = UserPreferenceState(user_id=user_id, computed_at=computed_at)
        session.add(state)

    preferences = list(
        await session.scalars(select(UserPreference).where(UserPreference.user_id == user_id))
    )
    by_key = {(preference.axis, preference.subject): preference for preference in preferences}
    for preference in preferences:
        preference.raw_score *= _decay_factor(preference.computed_at, computed_at)
        preference.computed_at = computed_at

    events = await _unseen_events(session, user_id, state)
    for event in events:
        weight = await _event_weight(session, event)
        if weight:
            for axis, subject in await _event_targets(session, event):
                key = (axis.value, subject)
                preference = by_key.get(key)
                if preference is None:
                    preference = UserPreference(
                        user_id=user_id,
                        axis=axis.value,
                        subject=subject,
                        raw_score=0,
                        score=0,
                        computed_at=computed_at,
                    )
                    session.add(preference)
                    preferences.append(preference)
                    by_key[key] = preference
                preference.raw_score += weight * _decay_factor(event.created_at, computed_at)
        state.last_event_created_at = event.created_at
        state.last_event_id = event.id

    _normalise(preferences)
    state.computed_at = computed_at
    await session.flush()
    return preferences


async def rebuild_user_interest_profile(
    session: AsyncSession, user_id: UUID, *, now: datetime | None = None
) -> list[UserPreference]:
    """Rebuild one profile for verification or recovery from stored events."""

    await session.execute(
        delete(UserPreference)
        .where(UserPreference.user_id == user_id)
        .execution_options(synchronize_session="fetch")
    )
    await session.execute(
        delete(UserPreferenceState)
        .where(UserPreferenceState.user_id == user_id)
        .execution_options(synchronize_session="fetch")
    )
    await session.flush()
    return await refresh_user_interest_profile(session, user_id, now=now)


async def _unseen_events(
    session: AsyncSession, user_id: UUID, state: UserPreferenceState
) -> list[UserEvent]:
    statement = select(UserEvent).where(UserEvent.user_id == user_id)
    if state.last_event_created_at is not None and state.last_event_id is not None:
        statement = statement.where(
            or_(
                UserEvent.created_at > state.last_event_created_at,
                and_(
                    UserEvent.created_at == state.last_event_created_at,
                    UserEvent.id > state.last_event_id,
                ),
            )
        )
    return list(await session.scalars(statement.order_by(UserEvent.created_at, UserEvent.id)))


async def _event_weight(session: AsyncSession, event: UserEvent) -> float:
    event_type = UserEventType(event.event_type)
    if event_type is UserEventType.PROGRESS:
        if event.value is None or event.value < 50 or event.media_id is None:
            return 0
        prior = await session.scalar(
            select(UserEvent.id)
            .where(
                UserEvent.user_id == event.user_id,
                UserEvent.media_id == event.media_id,
                UserEvent.event_type == UserEventType.PROGRESS.value,
                UserEvent.value >= 50,
                or_(
                    UserEvent.created_at < event.created_at,
                    and_(UserEvent.created_at == event.created_at, UserEvent.id < event.id),
                ),
            )
            .limit(1)
        )
        return 0 if prior is not None else 3
    return EVENT_WEIGHTS.get(event_type, 0)


async def _event_targets(
    session: AsyncSession, event: UserEvent
) -> list[tuple[PreferenceAxis, str]]:
    event_type = UserEventType(event.event_type)
    if event.subject_id is not None and event_type is UserEventType.HIDE_TAG:
        return [(PreferenceAxis.TAG, str(event.subject_id))]
    if event.subject_id is not None and event_type is UserEventType.HIDE_PERFORMER:
        return [(PreferenceAxis.PERFORMER, str(event.subject_id))]
    if event.media_id is None:
        return []

    media = await session.get(Media, event.media_id)
    if media is None:
        return []
    tags = await session.scalars(select(MediaTag.tag_id).where(MediaTag.media_id == media.id))
    performers = await session.scalars(
        select(MediaPerformer.performer_id).where(MediaPerformer.media_id == media.id)
    )
    targets = [(PreferenceAxis.TAG, str(tag_id)) for tag_id in tags]
    targets.extend((PreferenceAxis.PERFORMER, str(performer_id)) for performer_id in performers)
    if media.studio:
        targets.append((PreferenceAxis.STUDIO, media.studio.casefold()))
    quality = await session.scalar(
        select(MediaFile.quality).where(
            MediaFile.media_id == media.id,
            MediaFile.is_active.is_(True),
            MediaFile.quality.is_not(None),
        )
    )
    if quality:
        targets.append((PreferenceAxis.QUALITY, quality))
    return targets


def _normalise(preferences: list[UserPreference]) -> None:
    grouped: dict[str, list[UserPreference]] = defaultdict(list)
    for preference in preferences:
        grouped[preference.axis].append(preference)
    for values in grouped.values():
        maximum = max((preference.raw_score for preference in values), default=0)
        denominator = maximum if maximum > 0 else 1
        for preference in values:
            preference.score = max(preference.raw_score, 0) / denominator
