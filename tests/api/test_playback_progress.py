"""Playback progress and per-user resume behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media
from pornarr_db.models.playback import PlaybackProgress, UserEvent
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def _media(app, title: str) -> Media:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(title=title, normalized_title=title.casefold())
        session.add(media)
        await session.commit()
    return media


async def _report(client: AsyncClient, media: Media, position_seconds: float) -> dict[str, object]:
    response = await client.post(
        f"/api/playback/{media.id}/progress",
        json={"position_seconds": position_seconds, "duration_seconds": 100},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    return response.json()


async def test_progress_is_per_user_and_resumes_at_the_recorded_position(app, client) -> None:
    media = await _media(app, "Example")
    alice = await create_user(app)
    bob = await create_user(app, username="bob")
    await login(client, alice.username, "correct horse battery staple")

    alice_progress = await _report(client, media, 42.5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob_client:
        await login(bob_client, bob.username, "correct horse battery staple")
        bob_progress = await _report(bob_client, media, 7)
        assert (await bob_client.get(f"/api/playback/{media.id}/progress")).json() == bob_progress

    assert (await client.get(f"/api/playback/{media.id}/progress")).json() == alice_progress
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        stored = list(await session.scalars(select(PlaybackProgress)))
    assert {(progress.user_id, progress.position_seconds) for progress in stored} == {
        (alice.id, 42.5),
        (bob.id, 7),
    }


async def test_completion_is_recorded_once_and_continue_watching_is_ordered_by_recency(
    app, client
) -> None:
    first = await _media(app, "First")
    second = await _media(app, "Second")
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await _report(client, first, 89))["completed"] is False
    assert (await _report(client, first, 90))["completed"] is True
    assert (await _report(client, first, 100))["completed"] is True
    await _report(client, second, 10)

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        first_progress = await session.scalar(
            select(PlaybackProgress).where(
                PlaybackProgress.user_id == user.id, PlaybackProgress.media_id == first.id
            )
        )
        assert first_progress is not None
        first_progress.completed = False
        first_progress.updated_at = datetime.now(UTC) - timedelta(minutes=1)
        events = list(await session.scalars(select(UserEvent)))
        await session.commit()

    assert [event.event_type for event in events] == [
        "play",
        "progress",
        "progress",
        "completed",
        "progress",
        "play",
        "progress",
    ]
    continue_watching = await client.get("/api/playback/continue-watching")
    assert continue_watching.status_code == 200
    assert [item["media_id"] for item in continue_watching.json()] == [
        str(second.id),
        str(first.id),
    ]


async def test_progress_rejects_a_position_beyond_the_duration(app, client) -> None:
    media = await _media(app, "Example")
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(
        f"/api/playback/{media.id}/progress",
        json={"position_seconds": 101, "duration_seconds": 100},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


async def test_completion_uses_the_configured_threshold(app, client) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"playback_completion_threshold_percent": 75}
    )
    media = await _media(app, "Example")
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await _report(client, media, 74.9))["completed"] is False
    assert (await _report(client, media, 75))["completed"] is True
