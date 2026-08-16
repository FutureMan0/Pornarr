"""The scene index a title exposes, and how a replaced file invalidates it."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_media.scenes import Scene
from pornarr_worker.scenes import replace_scene_markers
from tests.api.test_auth import create_user, login

pytest_plugins = ("tests.api.test_auth",)

PASSWORD = "correct horse battery staple"


async def _media_with_file(app: FastAPI, *, path: str = "/data/a.mkv") -> tuple[Media, MediaFile]:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(title="Aurora 214", normalized_title="aurora 214")
        session.add(media)
        await session.flush()
        media_file = MediaFile(media_id=media.id, path=path, size=1024, duration_seconds=60)
        session.add(media_file)
        await session.commit()
    return media, media_file


async def _add_markers(app: FastAPI, media_file_id, scenes: tuple[Scene, ...]) -> int:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        written = await replace_scene_markers(session, media_file_id, scenes)
        await session.commit()
    return written


async def test_the_scene_index_comes_back_in_order_with_durations(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)
    media, media_file = await _media_with_file(app)
    await _add_markers(
        app,
        media_file.id,
        (
            Scene(ordinal=0, start_seconds=0.0, end_seconds=12.5),
            Scene(ordinal=1, start_seconds=12.5, end_seconds=60.0),
        ),
    )

    response = await client.get(f"/api/media/{media.id}/scenes")

    assert response.status_code == 200
    body = response.json()
    assert body["media_file_id"] == str(media_file.id)
    assert [(s["ordinal"], s["start_seconds"], s["duration_seconds"]) for s in body["scenes"]] == [
        (0, 0.0, 12.5),
        (1, 12.5, 47.5),
    ]


async def test_a_title_with_no_analysis_yet_reports_an_empty_index(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)
    media, _ = await _media_with_file(app)

    response = await client.get(f"/api/media/{media.id}/scenes")

    assert response.status_code == 200
    assert response.json()["scenes"] == []


async def test_an_unknown_title_is_404(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)

    response = await client.get(f"/api/media/{uuid4()}/scenes")

    assert response.status_code == 404


async def test_the_index_requires_signing_in(app: FastAPI, client: AsyncClient) -> None:
    # A user has to exist, or the request meets the not-yet-configured gate
    # before it ever reaches authentication.
    await create_user(app, username="alice")
    media, _ = await _media_with_file(app)

    response = await client.get(f"/api/media/{media.id}/scenes")

    assert response.status_code == 401


async def test_re_analysis_replaces_the_index_rather_than_appending(
    app: FastAPI, client: AsyncClient
) -> None:
    """New boundaries against old ordinals would be an index matching neither."""

    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)
    media, media_file = await _media_with_file(app)
    await _add_markers(
        app,
        media_file.id,
        (
            Scene(ordinal=0, start_seconds=0.0, end_seconds=20.0),
            Scene(ordinal=1, start_seconds=20.0, end_seconds=40.0),
            Scene(ordinal=2, start_seconds=40.0, end_seconds=60.0),
        ),
    )

    written = await _add_markers(
        app, media_file.id, (Scene(ordinal=0, start_seconds=0.0, end_seconds=60.0),)
    )
    body = (await client.get(f"/api/media/{media.id}/scenes")).json()

    assert written == 1
    assert [
        {key: value for key, value in scene.items() if key != "id"} for scene in body["scenes"]
    ] == [{"ordinal": 0, "start_seconds": 0.0, "end_seconds": 60.0, "duration_seconds": 60.0}]


def test_markers_are_declared_to_die_with_their_file() -> None:
    """A stale index pointing into a video nobody has is worse than none.

    Asserted on the schema rather than by deleting a row: this suite runs on
    SQLite, which does not enforce `ON DELETE CASCADE` unless foreign keys are
    switched on per connection, so a deletion here would prove nothing about
    the PostgreSQL the application actually runs on.
    """
    foreign_key = next(iter(SceneMarker.__table__.c.media_file_id.foreign_keys))

    assert foreign_key.column.table.name == "media_files"
    assert foreign_key.ondelete == "CASCADE"
