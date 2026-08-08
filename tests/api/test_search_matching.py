"""Library classifications for external indexer releases."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_api.services.search_matching import SearchMatchKind, match_release
from pornarr_db.base import Base
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.release_cache import normalize_release_title


async def test_matching_returns_the_library_link_and_interim_resolution_badges() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            media = Media(
                title="Example Scene", normalized_title=normalize_release_title("Example Scene")
            )
            media.files.append(
                MediaFile(path="/library/example.mp4", size=1_000, resolution="1080p")
            )
            session.add(media)
            await session.commit()
            media_id = media.id

            upgrade = await match_release(session, "Example-Scene 2160p")
            present = await match_release(session, "Example Scene 720p")
            new = await match_release(session, "Different Scene 2160p")

        assert isinstance(media_id, UUID)
        assert upgrade.kind is SearchMatchKind.UPGRADE
        assert upgrade.media_id == media_id
        assert present.kind is SearchMatchKind.PRESENT
        assert present.media_id == media_id
        assert new.kind is SearchMatchKind.NEW
        assert new.media_id is None
    finally:
        await engine.dispose()
