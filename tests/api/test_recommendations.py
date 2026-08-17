"""Private recommendation listing and immediate feedback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import MediaTag, Tag
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.preferences import PreferenceAxis, UserPreference, UserPreferenceState
from pornarr_db.models.recommendation import RecommendationCandidate
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def _media(session: AsyncSession, title: str, tag: Tag | None = None) -> Media:
    media = Media(title=title, normalized_title=title.casefold())
    session.add(media)
    await session.flush()
    session.add(MediaFile(media_id=media.id, path=f"/library/{media.id}.mkv", size=1))
    if tag is not None:
        session.add(MediaTag(media_id=media.id, tag_id=tag.id, confidence=1, source="test"))
    return media


async def test_recommendations_are_private_and_expired_candidates_are_hidden(app, client) -> None:
    user = await create_user(app)
    other = await create_user(app, username="other")
    now = datetime.now(UTC)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = await _media(session, "Visible")
        expired = await _media(session, "Expired")
        hidden = await _media(session, "Other")
        session.add_all(
            (
                RecommendationCandidate(
                    user_id=user.id,
                    media_id=media.id,
                    score=0.8,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.8}},
                    model_version="v1",
                    expires_at=now + timedelta(days=1),
                ),
                RecommendationCandidate(
                    user_id=user.id,
                    media_id=expired.id,
                    score=0.9,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.9}},
                    model_version="v1",
                    expires_at=now - timedelta(seconds=1),
                ),
                RecommendationCandidate(
                    user_id=other.id,
                    media_id=hidden.id,
                    score=1,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 1}},
                    model_version="v1",
                    expires_at=now + timedelta(days=1),
                ),
            )
        )
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/recommendations")
    forbidden_feedback = await client.post(
        f"/api/recommendations/{hidden.id}/feedback",
        json={"event_type": "not_interested"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert [item["media_id"] for item in response.json()] == [str(media.id)]
    assert forbidden_feedback.status_code == 404


async def test_reasons_name_the_signals_that_carried_the_score(app, client) -> None:
    """The card prints sentences, strongest signal first, and nothing that scored zero."""

    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = await _media(session, "Explained")
        session.add(
            RecommendationCandidate(
                user_id=user.id,
                media_id=media.id,
                score=0.6,
                reason_json={
                    "matched_tags": ["neon"],
                    "matched_performers": ["ada"],
                    "matched_studios": [],
                    "dominant_factor": "tag",
                    "score_breakdown": {
                        "tag": 0.3,
                        "performer": 0.12,
                        "studio": 0,
                        "quality": 0.1,
                        "recency": 0,
                        "popularity": 0,
                        "rating": 0.08,
                    },
                },
                model_version="v1",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    entry = (await client.get("/api/recommendations")).json()[0]

    assert entry["match_score"] == 60
    assert entry["reasons"] == [
        "Tags you keep watching",
        "A performer you follow",
        "Matches the quality you prefer",
        "Rated highly here",
    ]


async def test_not_interested_removes_a_recommendation_immediately(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = await _media(session, "Not interested")
        session.add(
            RecommendationCandidate(
                user_id=user.id,
                media_id=media.id,
                score=0.8,
                reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.8}},
                model_version="v1",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(
        f"/api/recommendations/{media.id}/feedback",
        json={"event_type": "not_interested"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 204
    assert (await client.get("/api/recommendations")).json() == []


async def test_hiding_a_tag_records_feedback_and_removes_all_matching_candidates(
    app, client
) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        tag = Tag(name="Hide", normalized_name="hide")
        session.add(tag)
        await session.flush()
        first = await _media(session, "First", tag)
        second = await _media(session, "Second", tag)
        session.add_all(
            (
                RecommendationCandidate(
                    user_id=user.id,
                    media_id=first.id,
                    score=0.8,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.8}},
                    model_version="v1",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
                RecommendationCandidate(
                    user_id=user.id,
                    media_id=second.id,
                    score=0.7,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.7}},
                    model_version="v1",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            )
        )
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(
        f"/api/recommendations/{first.id}/feedback",
        json={"event_type": "hide_tag", "subject_id": str(tag.id)},
        headers=csrf_headers(client),
    )

    assert response.status_code == 204
    assert (await client.get("/api/recommendations")).json() == []
    async with AsyncSession(app.state.engine) as session:
        event = await session.scalar(select(UserEvent).where(UserEvent.user_id == user.id))
    assert event is not None
    assert event.event_type == "hide_tag"


async def test_reset_removes_events_preferences_and_candidates(app, client) -> None:
    user = await create_user(app)
    now = datetime.now(UTC)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = await _media(session, "Reset")
        session.add_all(
            (
                UserEvent(user_id=user.id, event_type=UserEventType.VIEW, media_id=media.id),
                UserPreference(
                    user_id=user.id,
                    axis=PreferenceAxis.TAG,
                    subject="tag",
                    raw_score=1,
                    score=1,
                    computed_at=now,
                ),
                UserPreferenceState(user_id=user.id, computed_at=now),
                RecommendationCandidate(
                    user_id=user.id,
                    media_id=media.id,
                    score=0.8,
                    reason_json={"dominant_factor": "tag", "score_breakdown": {"tag": 0.8}},
                    model_version="v1",
                    expires_at=now + timedelta(days=1),
                ),
            )
        )
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.delete("/api/recommendations/profile", headers=csrf_headers(client))

    assert response.status_code == 204
    async with AsyncSession(app.state.engine) as session:
        assert (
            list(await session.scalars(select(UserEvent).where(UserEvent.user_id == user.id))) == []
        )
        assert (
            list(
                await session.scalars(
                    select(UserPreference).where(UserPreference.user_id == user.id)
                )
            )
            == []
        )
        assert await session.get(UserPreferenceState, user.id) is None
        assert (
            list(
                await session.scalars(
                    select(RecommendationCandidate).where(
                        RecommendationCandidate.user_id == user.id
                    )
                )
            )
            == []
        )
