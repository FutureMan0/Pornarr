"""Stored recommendation candidates and diversity limits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.preferences import PreferenceAxis, UserPreference
from pornarr_db.models.user import User
from pornarr_db.recommendations import generate_recommendations


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


async def _media(session: AsyncSession, number: int, tag: Tag, performer: Performer) -> Media:
    media = Media(
        title=f"Example {number}",
        normalized_title=f"example {number}",
        studio=f"Studio {number % 4}",
    )
    session.add(media)
    await session.flush()
    session.add_all(
        (
            MediaFile(media_id=media.id, path=f"/library/{number}.mkv", size=1, quality="1080p"),
            MediaTag(media_id=media.id, tag_id=tag.id, confidence=1, source="test"),
            MediaPerformer(media_id=media.id, performer_id=performer.id),
        )
    )
    return media


async def test_generation_stores_explainable_diverse_candidates(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    liked = Tag(name="Liked", normalized_name="liked")
    explore = Tag(name="Explore", normalized_name="explore")
    dominant = Performer(name="Dominant", normalized_name="dominant")
    session.add_all((user, liked, explore, dominant))
    await session.flush()
    media = [
        await _media(session, number, liked if number < 20 else explore, dominant)
        for number in range(24)
    ]
    session.add(
        UserPreference(
            user_id=user.id,
            axis=PreferenceAxis.TAG.value,
            subject=str(liked.id),
            raw_score=8,
            score=1,
            computed_at=datetime.now(UTC),
        )
    )
    await session.flush()

    now = datetime(2026, 8, 11, tzinfo=UTC)
    candidates = await generate_recommendations(session, user.id, now=now)

    assert len(candidates) <= 3
    assert {candidate.media_id for candidate in candidates} <= {item.id for item in media}
    assert all(
        cast(str, candidate.reason_json["dominant_factor"])
        in cast(dict[str, float], candidate.reason_json["score_breakdown"])
        for candidate in candidates
    )
    assert all(candidate.reason_json["matched_tags"] for candidate in candidates)
    assert all(
        candidate.score
        == pytest.approx(
            sum(cast(dict[str, float], candidate.reason_json["score_breakdown"]).values())
        )
        for candidate in candidates
    )
    assert all(
        candidate.model_version == "v1" and candidate.expires_at == now + timedelta(days=1)
        for candidate in candidates
    )
