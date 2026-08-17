from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import PlaybackProgress
from pornarr_db.models.user import User
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, csrf_headers, login


@pytest.fixture
async def app() -> AsyncIterator:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(build_settings())
    application.state.engine, application.state.redis = engine, MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def test_library_browses_active_media_with_user_progress(app, client: AsyncClient) -> None:
    user: User = await create_user(app)
    factory = async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        media = Media(title="Sample", normalized_title="sample")
        session.add(
            MediaFile(media=media, path="/data/library/sample.mp4", size=1, quality="1080p")
        )
        await session.flush()
        session.add(
            PlaybackProgress(
                user_id=user.id, media_id=media.id, position_seconds=10, duration_seconds=60
            )
        )
        await session.commit()
    assert (await client.get("/api/library")).status_code == 401
    await login(client, user.username, "correct horse battery staple")
    response = await client.get("/api/library", params={"limit": 1})
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": str(media.id),
            "title": "Sample",
            "studio": None,
            "release_date": None,
            "duration_seconds": None,
            "quality": "1080p",
            "resolution": None,
            "position_seconds": 10,
            "progress_duration_seconds": 60,
            "completed": False,
            "poster_url": f"/api/media/{media.id}/poster",
            "sprite_url": f"/api/media/{media.id}/sprite",
            "rating": None,
            "rating_count": 0,
            "tag_count": 0,
            "comment_count": 0,
        }
    ]
    correction = await client.post(
        f"/api/media/{media.id}/tags", json={"name": "Verified"}, headers=csrf_headers(client)
    )
    assert correction.status_code == 201
    assert correction.json() == {"name": "Verified", "confidence": 1, "source": "manual"}
    detail = await client.get(f"/api/media/{media.id}")
    assert detail.status_code == 200
    assert detail.json()["tags"] == [correction.json()]
    assert detail.json()["path"] == "/data/library/sample.mp4"
