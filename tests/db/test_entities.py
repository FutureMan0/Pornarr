from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.entities import merge_performers, preferred_tag_assignment
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer
from pornarr_db.models.media import Media


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as database_session:
        yield database_session
    await engine.dispose()


async def test_merging_performers_preserves_all_media_links(session: AsyncSession) -> None:
    source = Performer(name="Alicia", normalized_name="alicia")
    target = Performer(name="Alice", normalized_name="alice")
    first = Media(title="One", normalized_title="one")
    shared = Media(title="Two", normalized_title="two")
    session.add_all([source, target, first, shared])
    await session.flush()
    session.add_all(
        [
            MediaPerformer(media_id=first.id, performer_id=source.id),
            MediaPerformer(media_id=shared.id, performer_id=source.id),
            MediaPerformer(media_id=shared.id, performer_id=target.id),
        ]
    )
    await session.flush()

    await merge_performers(session, source.id, target.id)
    await session.commit()

    links = set(await session.scalars(select(MediaPerformer.media_id)))
    assert links == {first.id, shared.id}
    assert await session.get(Performer, source.id) is None


def test_user_tag_assignment_outranks_automatic_assignment() -> None:
    automatic = MediaTag(
        id=uuid4(), media_id=uuid4(), tag_id=uuid4(), confidence=0.99, source="metadata"
    )
    correction = MediaTag(
        id=uuid4(),
        media_id=automatic.media_id,
        tag_id=automatic.tag_id,
        confidence=1.0,
        source="user",
    )

    assert preferred_tag_assignment([automatic, correction]) is correction


def test_no_tag_assignment_has_no_effective_source() -> None:
    assert preferred_tag_assignment([]) is None
