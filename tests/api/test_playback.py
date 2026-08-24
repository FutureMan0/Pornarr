from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_db.base import Base
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent
from pornarr_db.models.user import User
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, login


@pytest.fixture
async def app() -> AsyncIterator:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(build_settings())
    application.state.engine = engine
    application.state.redis = MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def create_media_file(app) -> MediaFile:
    session_factory = async_sessionmaker(
        app.state.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        media = Media(title="Sample", normalized_title="sample")
        media_file = MediaFile(
            media=media,
            path="/data/library/sample.mp4",
            size=1,
            codecs={
                "container": "mp4",
                "video": {"codec": "h264", "profile": "high", "level": 4.1},
                "audio": {"codec": "aac"},
            },
        )
        session.add(media_file)
        await session.commit()
    return media_file


async def test_playback_info_reports_direct_play_for_the_default_browser(app, client) -> None:
    user: User = await create_user(app)
    media_file = await create_media_file(app)
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{media_file.media_id}/playback-info")

    assert response.status_code == 200
    assert response.json() == {"direct_play": True, "reasons": []}
    async with AsyncSession(app.state.engine) as session:
        event = await session.scalar(select(UserEvent))
    assert event is not None
    assert event.event_type == "view"
    assert event.media_id == media_file.media_id


async def test_playback_info_returns_machine_readable_incompatibility_reasons(app, client) -> None:
    user: User = await create_user(app)
    media_file = await create_media_file(app)
    unsupported_codecs: dict[str, object] = {
        "container": "mp4",
        "video": {"codec": "hevc", "profile": "main", "level": 4.1},
        "audio": {"codec": "aac"},
    }
    media_file.codecs = unsupported_codecs
    session_factory = async_sessionmaker(
        app.state.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        await session.merge(media_file)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    response = await client.get(f"/api/media/{media_file.media_id}/playback-info")

    assert response.status_code == 200
    assert response.json() == {"direct_play": False, "reasons": ["video_codec_unsupported"]}

    supported = await client.get(
        f"/api/media/{media_file.media_id}/playback-info",
        params={
            "containers": "mp4",
            "video_codecs": "hevc",
            "audio_codecs": "aac",
            "video_profiles": "main",
            "maximum_video_level": 4.1,
        },
    )

    assert supported.status_code == 200
    assert supported.json() == {"direct_play": True, "reasons": []}


async def test_playback_info_handles_missing_or_invalid_technical_metadata(app, client) -> None:
    user: User = await create_user(app)
    media_file = await create_media_file(app)
    invalid_codecs: dict[str, object] = {"container": "mp4", "video": {"level": "invalid"}}
    media_file.codecs = invalid_codecs
    session_factory = async_sessionmaker(
        app.state.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        await session.merge(media_file)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    invalid = await client.get(f"/api/media/{media_file.media_id}/playback-info")
    missing = await client.get(f"/api/media/{uuid4()}/playback-info")

    assert invalid.json() == {
        "direct_play": False,
        "reasons": [
            "video_codec_unknown",
            "video_profile_unknown",
            "video_level_unknown",
        ],
    }
    assert missing.status_code == 404
