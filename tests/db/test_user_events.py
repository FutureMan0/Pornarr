"""Privacy-preserving user-event persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.events import prune_user_events, record_user_event
from pornarr_db.models.media import Media
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.user import User


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


async def test_user_events_are_repeatable_and_indexed_for_profile_queries(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    media = Media(title="Example", normalized_title="example")
    session.add_all((user, media))
    await session.flush()

    await record_user_event(session, user.id, UserEventType.PROGRESS, media_id=media.id, value=10)
    await record_user_event(session, user.id, UserEventType.PROGRESS, media_id=media.id, value=20)
    await session.flush()

    events = list(await session.scalars(select(UserEvent).where(UserEvent.user_id == user.id)))
    assert {(event.event_type, event.value) for event in events} == {
        (UserEventType.PROGRESS.value, 10),
        (UserEventType.PROGRESS.value, 20),
    }
    assert {index.name for index in Base.metadata.tables["user_events"].indexes} >= {
        "ix_user_events_user_id_created_at"
    }


async def test_event_retention_prunes_only_expired_records_and_user_deletion_cascades(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    media = Media(title="Example", normalized_title="example")
    session.add_all((user, media))
    await session.flush()
    expired = await record_user_event(session, user.id, UserEventType.VIEW, media_id=media.id)
    retained = await record_user_event(session, user.id, UserEventType.PLAY, media_id=media.id)
    expired.created_at = datetime.now(UTC) - timedelta(days=31)
    await session.flush()

    await prune_user_events(session, retention_days=30)
    assert list(await session.scalars(select(UserEvent))) == [retained]

    foreign_keys = UserEvent.__table__.c.user_id.foreign_keys
    assert {foreign_key.ondelete for foreign_key in foreign_keys} == {"CASCADE"}
    assert expired.id != retained.id


async def test_preference_events_retain_only_opaque_subject_identifiers(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    await session.flush()

    tag_id = uuid4()
    event = await record_user_event(
        session,
        user.id,
        UserEventType.HIDE_TAG,
        subject_id=tag_id,
    )

    assert event.subject_id == tag_id
    assert event.media_id is None
    assert event.value is None
