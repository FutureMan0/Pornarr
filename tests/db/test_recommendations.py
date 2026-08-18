"""Stored recommendation candidates and diversity limits."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.base import Base
from pornarr_db.events import record_user_event
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress, UserEventType
from pornarr_db.models.preferences import PreferenceAxis, UserPreference
from pornarr_db.models.social import MediaSend, Rating
from pornarr_db.models.user import User
from pornarr_db.preferences import refresh_user_interest_profile
from pornarr_db.recommendations import RecommendationOptions, generate_recommendations


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


async def _media(
    session: AsyncSession,
    number: int,
    tag: Tag,
    performer: Performer,
    *,
    studio: str | None = None,
    duration_seconds: float | None = None,
) -> Media:
    media = Media(
        title=f"Example {number}",
        normalized_title=f"example {number}",
        studio=studio or f"Studio {number % 4}",
    )
    session.add(media)
    await session.flush()
    session.add_all(
        (
            MediaFile(
                media_id=media.id,
                path=f"/library/{number}.mkv",
                size=1,
                quality="1080p",
                duration_seconds=duration_seconds,
            ),
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


async def test_distinct_event_histories_produce_isolated_candidate_scores(
    session: AsyncSession,
) -> None:
    alice = User(username="alice", password_hash="hash")
    bob = User(username="bob", password_hash="hash")
    alice_tag = Tag(name="Alice", normalized_name="alice")
    bob_tag = Tag(name="Bob", normalized_name="bob")
    alice_performer = Performer(name="Alice performer", normalized_name="alice performer")
    bob_performer = Performer(name="Bob performer", normalized_name="bob performer")
    session.add_all((alice, bob, alice_tag, bob_tag, alice_performer, bob_performer))
    await session.flush()
    alice_signal = await _media(session, 1, alice_tag, alice_performer)
    alice_candidate = await _media(session, 2, alice_tag, alice_performer)
    bob_signal = await _media(session, 3, bob_tag, bob_performer)
    bob_candidate = await _media(session, 4, bob_tag, bob_performer)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    for user, signal in ((alice, alice_signal), (bob, bob_signal)):
        event = await record_user_event(
            session, user.id, UserEventType.FAVOURITE, media_id=signal.id
        )
        event.created_at = now
    await session.flush()

    await refresh_user_interest_profile(session, alice.id, now=now)
    await refresh_user_interest_profile(session, bob.id, now=now)
    alice_scores = {
        candidate.media_id: candidate.score
        for candidate in await generate_recommendations(session, alice.id, now=now)
    }
    bob_scores = {
        candidate.media_id: candidate.score
        for candidate in await generate_recommendations(session, bob.id, now=now)
    }

    assert alice_scores[alice_candidate.id] > alice_scores[bob_candidate.id]
    assert bob_scores[bob_candidate.id] > bob_scores[alice_candidate.id]


async def test_generation_enforces_performer_and_studio_limits(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    tag = Tag(name="Liked", normalized_name="liked")
    shared_performer = Performer(name="Shared", normalized_name="shared")
    session.add_all((user, tag, shared_performer))
    await session.flush()
    media = [
        await _media(session, number, tag, shared_performer, studio=f"Studio {number}")
        for number in range(5)
    ]
    for number in range(5, 26):
        performer = Performer(name=f"Performer {number}", normalized_name=f"performer {number}")
        session.add(performer)
        media.append(
            await _media(
                session,
                number,
                tag,
                performer,
                studio="Popular Studio" if number < 10 else f"Studio {number}",
            )
        )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    session.add(
        UserPreference(
            user_id=user.id,
            axis=PreferenceAxis.TAG.value,
            subject=str(tag.id),
            raw_score=8,
            score=1,
            computed_at=now,
        )
    )
    await session.flush()

    candidates = await generate_recommendations(session, user.id, now=now)
    performers = {
        item.id: str(shared_performer.id) if item in media[:5] else str(item.id) for item in media
    }
    studios = {item.id: item.studio for item in media}

    assert len(candidates) == 20
    assert max(Counter(performers[candidate.media_id] for candidate in candidates).values()) <= 2
    assert max(Counter(studios[candidate.media_id] for candidate in candidates).values()) <= 3


async def test_generation_never_stores_hard_blocked_metadata(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    allowed_tag = Tag(name="Allowed", normalized_name="allowed")
    blocked_tag = Tag(name="Blocked", normalized_name="blocked")
    allowed_performer = Performer(name="Allowed", normalized_name="allowed")
    blocked_performer = Performer(name="Blocked", normalized_name="blocked")
    session.add_all((user, allowed_tag, blocked_tag, allowed_performer, blocked_performer))
    await session.flush()
    allowed = await _media(session, 1, allowed_tag, allowed_performer)
    tag_blocked = await _media(session, 2, blocked_tag, allowed_performer)
    performer_blocked = await _media(session, 3, allowed_tag, blocked_performer)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    session.add_all(
        (
            UserPreference(
                user_id=user.id,
                axis=PreferenceAxis.TAG.value,
                subject=str(blocked_tag.id),
                raw_score=-100,
                score=0,
                computed_at=now,
            ),
            UserPreference(
                user_id=user.id,
                axis=PreferenceAxis.PERFORMER.value,
                subject=str(blocked_performer.id),
                raw_score=-100,
                score=0,
                computed_at=now,
            ),
        )
    )
    await session.flush()

    candidates = await generate_recommendations(session, user.id, now=now)

    assert {candidate.media_id for candidate in candidates} == {allowed.id}
    assert {tag_blocked.id, performer_blocked.id}.isdisjoint(
        {candidate.media_id for candidate in candidates}
    )


async def test_ratings_reach_the_score_only_while_the_switch_is_on(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    tag = Tag(name="Liked", normalized_name="liked")
    performer = Performer(name="Star", normalized_name="star")
    session.add_all((user, tag, performer))
    await session.flush()
    rated = await _media(session, 1, tag, performer)
    unrated = await _media(session, 2, tag, performer)
    session.add(Rating(user_id=user.id, media_id=rated.id, stars=5))
    await session.flush()
    now = datetime(2026, 8, 11, tzinfo=UTC)

    on = await _scores(session, user, now, RecommendationOptions(use_ratings=True))
    off = await _scores(session, user, now, RecommendationOptions(use_ratings=False))

    assert on[rated.id] > on[unrated.id]
    assert off[rated.id] == off[unrated.id]


async def test_friend_picks_leave_the_feed_when_the_switch_is_off(session: AsyncSession) -> None:
    alice = User(username="alice", password_hash="hash")
    bob = User(username="bob", password_hash="hash")
    tag = Tag(name="Liked", normalized_name="liked")
    performer = Performer(name="Star", normalized_name="star")
    session.add_all((alice, bob, tag, performer))
    await session.flush()
    picked = await _media(session, 1, tag, performer)
    await _media(session, 2, tag, performer)
    session.add(MediaSend(sender_id=bob.id, recipient_id=alice.id, media_id=picked.id))
    await session.flush()
    now = datetime(2026, 8, 11, tzinfo=UTC)

    on = await _scores(session, alice, now, RecommendationOptions(include_friend_picks=True))
    off = await _scores(session, alice, now, RecommendationOptions(include_friend_picks=False))

    assert picked.id in on
    assert picked.id not in off


async def test_finished_titles_are_hidden_only_while_the_switch_is_on(
    session: AsyncSession,
) -> None:
    user = User(username="alice", password_hash="hash")
    tag = Tag(name="Liked", normalized_name="liked")
    performer = Performer(name="Star", normalized_name="star")
    session.add_all((user, tag, performer))
    await session.flush()
    finished = await _media(session, 1, tag, performer)
    await _media(session, 2, tag, performer)
    session.add(
        PlaybackProgress(
            user_id=user.id,
            media_id=finished.id,
            position_seconds=1800,
            duration_seconds=1800,
            completed=True,
        )
    )
    await session.flush()
    now = datetime(2026, 8, 11, tzinfo=UTC)

    on = await _scores(session, user, now, RecommendationOptions(hide_finished=True))
    off = await _scores(session, user, now, RecommendationOptions(hide_finished=False))

    assert finished.id not in on
    assert finished.id in off


async def test_shorts_leave_the_feed_when_the_switch_is_off(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    tag = Tag(name="Liked", normalized_name="liked")
    performer = Performer(name="Star", normalized_name="star")
    session.add_all((user, tag, performer))
    await session.flush()
    short = await _media(session, 1, tag, performer, duration_seconds=45)
    feature = await _media(session, 2, tag, performer, duration_seconds=1800)
    await session.flush()
    now = datetime(2026, 8, 11, tzinfo=UTC)

    on = await _scores(session, user, now, RecommendationOptions(include_shorts=True))
    off = await _scores(session, user, now, RecommendationOptions(include_shorts=False))

    assert {short.id, feature.id} <= on.keys()
    assert off.keys() == {feature.id}


async def _scores(
    session: AsyncSession, user: User, now: datetime, options: RecommendationOptions
) -> dict[UUID, float]:
    return {
        candidate.media_id: candidate.score
        for candidate in await generate_recommendations(session, user.id, now=now, options=options)
    }
