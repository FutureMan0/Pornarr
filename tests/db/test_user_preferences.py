"""Incremental interest-profile computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.events import record_user_event
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEventType
from pornarr_db.models.preferences import PreferenceAxis, UserPreference, UserPreferenceState
from pornarr_db.models.user import User
from pornarr_db.preferences import (
    rebuild_user_interest_profile,
    refresh_interest_profiles,
    refresh_user_interest_profile,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def _media_with_metadata(session: AsyncSession) -> tuple[Media, Tag, Performer]:
    media = Media(title="Example", normalized_title="example", studio="Example Studio")
    tag = Tag(name="Example tag", normalized_name="example tag")
    performer = Performer(name="Example performer", normalized_name="example performer")
    session.add_all((media, tag, performer))
    await session.flush()
    session.add_all(
        (
            MediaFile(media_id=media.id, path="/library/example.mkv", size=1, quality="1080p"),
            MediaTag(media_id=media.id, tag_id=tag.id, confidence=1, source="test"),
            MediaPerformer(media_id=media.id, performer_id=performer.id),
        )
    )
    await session.flush()
    return media, tag, performer


def _preference_values(
    preferences: list[UserPreference],
) -> dict[tuple[str, str], tuple[float, float]]:
    return {
        (preference.axis, preference.subject): (preference.raw_score, preference.score)
        for preference in preferences
    }


async def test_favourite_updates_every_media_axis_with_ninety_day_decay(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    media, tag, performer = await _media_with_metadata(session)
    event = await record_user_event(session, user.id, UserEventType.FAVOURITE, media_id=media.id)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    event.created_at = now - timedelta(days=90)
    await session.flush()

    preferences = await refresh_user_interest_profile(session, user.id, now=now)

    assert _preference_values(preferences) == {
        (PreferenceAxis.TAG.value, str(tag.id)): (pytest.approx(4), pytest.approx(1)),
        (PreferenceAxis.PERFORMER.value, str(performer.id)): (pytest.approx(4), pytest.approx(1)),
        (PreferenceAxis.STUDIO.value, "example studio"): (pytest.approx(4), pytest.approx(1)),
        (PreferenceAxis.QUALITY.value, "1080p"): (pytest.approx(4), pytest.approx(1)),
    }


async def test_incremental_profile_matches_a_full_rebuild(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    media, tag, _ = await _media_with_metadata(session)
    start = datetime(2026, 5, 1, tzinfo=UTC)
    favourite = await record_user_event(
        session, user.id, UserEventType.FAVOURITE, media_id=media.id
    )
    favourite.created_at = start
    await session.flush()

    await refresh_user_interest_profile(session, user.id, now=start)
    rejected = await record_user_event(
        session, user.id, UserEventType.NOT_INTERESTED, media_id=media.id
    )
    finish = start + timedelta(days=30)
    rejected.created_at = finish
    await session.flush()

    incremental = await refresh_user_interest_profile(session, user.id, now=finish)
    rebuilt = await rebuild_user_interest_profile(session, user.id, now=finish)

    assert _preference_values(incremental) == _preference_values(rebuilt)
    assert _preference_values(rebuilt)[(PreferenceAxis.TAG.value, str(tag.id))][0] < 0


async def test_viewed_over_half_and_completed_media_add_their_weights_once(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    media, tag, _ = await _media_with_metadata(session)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    events = [
        await record_user_event(session, user.id, UserEventType.VIEW, media_id=media.id),
        await record_user_event(
            session, user.id, UserEventType.PROGRESS, media_id=media.id, value=25
        ),
        await record_user_event(
            session, user.id, UserEventType.PROGRESS, media_id=media.id, value=75
        ),
        await record_user_event(
            session, user.id, UserEventType.PROGRESS, media_id=media.id, value=90
        ),
        await record_user_event(session, user.id, UserEventType.COMPLETED, media_id=media.id),
    ]
    for offset, event in enumerate(events):
        event.created_at = now + timedelta(seconds=offset)
    await session.flush()

    preferences = await refresh_user_interest_profile(
        session, user.id, now=now + timedelta(minutes=1)
    )

    assert _preference_values(preferences)[(PreferenceAxis.TAG.value, str(tag.id))][
        0
    ] == pytest.approx(9, rel=1e-4)


async def test_hide_signals_are_hard_negative_preferences_and_empty_profiles_are_safe(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    empty_user = User(username="empty", password_hash="hash")
    session.add_all((user, empty_user))
    await session.flush()
    tag_id = uuid4()
    performer_id = uuid4()
    now = datetime(2026, 8, 11, tzinfo=UTC)
    events = (
        await record_user_event(session, user.id, UserEventType.HIDE_TAG, subject_id=tag_id),
        await record_user_event(
            session, user.id, UserEventType.HIDE_PERFORMER, subject_id=performer_id
        ),
    )
    for event in events:
        event.created_at = now
    await session.flush()

    preferences = await refresh_user_interest_profile(session, user.id, now=now)
    empty_preferences = await refresh_user_interest_profile(session, empty_user.id)
    refreshed = await refresh_user_interest_profile(session, user.id, now=now + timedelta(days=180))

    assert _preference_values(preferences) == {
        (PreferenceAxis.TAG.value, str(tag_id)): (pytest.approx(-100), pytest.approx(0)),
        (PreferenceAxis.PERFORMER.value, str(performer_id)): (
            pytest.approx(-100),
            pytest.approx(0),
        ),
    }
    assert _preference_values(refreshed) == _preference_values(preferences)
    assert len(refreshed) == 2
    assert empty_preferences == []
    assert await session.get(UserPreferenceState, empty_user.id) is not None
    assert list(await session.scalars(select(UserPreference))) == preferences


async def test_nightly_refresh_handles_users_without_events(session: AsyncSession) -> None:
    session.add_all(
        (User(username="alice", password_hash="hash"), User(username="bob", password_hash="hash"))
    )
    await session.flush()

    refreshed = await refresh_interest_profiles(session)

    assert refreshed == 2
    assert len(list(await session.scalars(select(UserPreferenceState)))) == 2
