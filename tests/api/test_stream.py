from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_api.routers.stream import ByteRange, RangeNotSatisfiableError, parse_byte_ranges
from pornarr_db.base import Base
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.user import User
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, login


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = build_settings().model_copy(update={"data_path": tmp_path})
    settings.library_path.mkdir()
    application = create_app(settings)
    application.state.engine = engine
    application.state.redis = MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def create_media_file(app: FastAPI, path: Path) -> MediaFile:
    session_factory = async_sessionmaker(
        app.state.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        media = Media(title="Sample", normalized_title="sample")
        media_file = MediaFile(media=media, path=str(path), size=path.stat().st_size)
        session.add(media_file)
        await session.commit()
    return media_file


def test_parses_single_open_ended_suffix_and_multi_ranges() -> None:
    assert parse_byte_ranges(None, 10) == ()
    assert parse_byte_ranges("bytes=0-2", 10) == (ByteRange(0, 2),)
    assert parse_byte_ranges("bytes=7-", 10) == (ByteRange(7, 9),)
    assert parse_byte_ranges("bytes=-3", 10) == (ByteRange(7, 9),)
    assert parse_byte_ranges("bytes=0-1,7-", 10) == (ByteRange(0, 1), ByteRange(7, 9))


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=5-3", "items=0-1", "bytes=-0"])
def test_rejects_unsatisfiable_ranges(header: str) -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_byte_ranges(header, 10)


async def test_stream_returns_correct_single_and_multi_range_responses(
    app: FastAPI, client: AsyncClient
) -> None:
    path = app.state.settings.library_path / "sample.mp4"
    path.write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    user: User = await create_user(app)
    media_file = await create_media_file(app, path)
    await login(client, user.username, "correct horse battery staple")

    single = await client.get(
        f"/api/media/{media_file.media_id}/stream", headers={"Range": "bytes=2-5"}
    )
    full = await client.get(f"/api/media/{media_file.media_id}/stream")
    open_ended = await client.get(
        f"/api/media/{media_file.media_id}/stream", headers={"Range": "bytes=24-"}
    )
    multiple = await client.get(
        f"/api/media/{media_file.media_id}/stream", headers={"Range": "bytes=0-1,24-"}
    )

    assert single.status_code == 206
    assert single.headers["accept-ranges"] == "bytes"
    assert single.headers["content-range"] == "bytes 2-5/26"
    assert single.content == b"cdef"
    assert full.status_code == 200
    assert full.headers["content-length"] == "26"
    assert full.content == b"abcdefghijklmnopqrstuvwxyz"
    assert open_ended.content == b"yz"
    assert multiple.status_code == 206
    assert multiple.headers["content-type"].startswith("multipart/byteranges; boundary=")
    assert multiple.headers["content-length"] == str(len(multiple.content))
    assert b"Content-Range: bytes 0-1/26" in multiple.content
    assert b"Content-Range: bytes 24-25/26" in multiple.content


async def test_stream_rejects_invalid_ranges_and_files_outside_the_library(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not a library file")
    user: User = await create_user(app)
    traversal_path = app.state.settings.library_path / ".." / outside.name
    media_file = await create_media_file(app, traversal_path)
    inside = app.state.settings.library_path / "inside.mp4"
    inside.write_bytes(b"inside")
    inside_media_file = await create_media_file(app, inside)
    await login(client, user.username, "correct horse battery staple")

    outside_response = await client.get(f"/api/media/{media_file.media_id}/stream")
    invalid_range = await client.get(
        f"/api/media/{inside_media_file.media_id}/stream", headers={"Range": "bytes=99-"}
    )

    assert outside_response.status_code == 404
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == "bytes */6"
    assert invalid_range.json()["code"] == "RANGE_NOT_SATISFIABLE"
