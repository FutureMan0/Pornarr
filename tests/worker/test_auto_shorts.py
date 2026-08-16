"""The nightly job that turns watch behaviour into clips."""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.hotspots import HotspotOptions
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.social import Short, ShortSource
from pornarr_db.models.user import User
from pornarr_worker.jobs.auto_shorts import create_automatic_shorts
from tests.api.test_auth import create_user

pytest_plugins = ("tests.api.test_auth",)

DURATION = 1200.0


async def _title(session: AsyncSession, title: str = "Aurora 214") -> tuple[Media, MediaFile]:
    media = Media(title=title, normalized_title=title.casefold())
    session.add(media)
    await session.flush()
    media_file = MediaFile(
        media_id=media.id, path=f"/data/{title}.mkv", size=1024, duration_seconds=DURATION
    )
    session.add(media_file)
    await session.flush()
    return media, media_file


def _progress(user_id: UUID, media_id: UUID, position_seconds: float) -> UserEvent:
    return UserEvent(
        user_id=user_id,
        media_id=media_id,
        event_type=UserEventType.PROGRESS.value,
        value=position_seconds * 100 / DURATION,
    )


async def _watch(
    session: AsyncSession, users: list[User], media_id: UUID, *, hotspot_at: float | None
) -> None:
    """Ordinary front-loaded viewing, optionally with one rewatched moment."""
    for index, user in enumerate(users):
        # Each viewer gets further than the last: the natural decay every title has.
        for step in range(10 + index * 10):
            session.add(_progress(user.id, media_id, step * 10.0))
        if hotspot_at is not None:
            for _ in range(30):
                session.add(_progress(user.id, media_id, hotspot_at))
    await session.flush()


async def test_a_rewatched_moment_becomes_a_clip_and_plain_decay_does_not(
    app: FastAPI,
) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        hot, _ = await _title(session, "Hot")
        plain, _ = await _title(session, "Plain")
        await _watch(session, users, hot.id, hotspot_at=600.0)
        await _watch(session, users, plain.id, hotspot_at=None)

        created = await create_automatic_shorts(session)
        shorts = list(await session.scalars(select(Short)))

    assert created == 1
    assert [short.media_id for short in shorts] == [hot.id]
    # Started just before the moment, one minute long.
    assert shorts[0].start_seconds == 595.0
    assert shorts[0].end_seconds == 655.0
    assert shorts[0].source is ShortSource.HOTSPOT
    assert shorts[0].title.startswith("Hot — 9:55")


async def test_a_title_only_one_person_watched_is_left_alone(app: FastAPI) -> None:
    """One person's habit is not the household's behaviour."""

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        user = await create_user(app, username="solo")
        media, _ = await _title(session)
        await _watch(session, [user], media.id, hotspot_at=600.0)

        created = await create_automatic_shorts(session)

    assert created == 0


async def test_running_twice_adds_nothing_the_second_time(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media, _ = await _title(session)
        await _watch(session, users, media.id, hotspot_at=600.0)

        first = await create_automatic_shorts(session)
        second = await create_automatic_shorts(session)
        total = len(list(await session.scalars(select(Short))))

    assert (first, second, total) == (1, 0, 1)


async def test_a_hand_made_clip_is_never_moved_or_duplicated(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media, _ = await _title(session)
        await _watch(session, users, media.id, hotspot_at=600.0)
        session.add(
            Short(
                media_id=media.id,
                title="by hand",
                start_seconds=590.0,
                end_seconds=650.0,
                source=ShortSource.MANUAL,
            )
        )
        await session.flush()

        created = await create_automatic_shorts(session)
        shorts = list(await session.scalars(select(Short)))

    assert created == 0
    assert [(short.title, short.source) for short in shorts] == [("by hand", ShortSource.MANUAL)]


async def test_a_clip_opens_on_a_scene_cut_when_one_is_in_reach(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media, media_file = await _title(session)
        await _watch(session, users, media.id, hotspot_at=600.0)
        session.add(
            SceneMarker(
                media_file_id=media_file.id,
                ordinal=0,
                start_seconds=597.0,
                end_seconds=700.0,
            )
        )
        await session.flush()

        await create_automatic_shorts(session)
        short = await session.scalar(select(Short))

    assert short is not None
    # 595.0 without a marker; the cut at 597.0 is inside the lead-in.
    assert short.start_seconds == 597.0


async def test_a_broad_hot_passage_gets_the_five_minute_cut(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media, _ = await _title(session)
        await _watch(session, users, media.id, hotspot_at=None)
        # A whole passage replayed, not a single moment.
        for user in users:
            for offset in range(0, 100, 10):
                for _ in range(30):
                    session.add(_progress(user.id, media.id, 600.0 + offset))
        await session.flush()

        await create_automatic_shorts(session)
        short = await session.scalar(select(Short))

    assert short is not None
    assert short.end_seconds - short.start_seconds == 300.0


async def test_a_title_with_no_active_file_is_skipped(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media = Media(title="No file", normalized_title="no file")
        session.add(media)
        await session.flush()
        await _watch(session, users, media.id, hotspot_at=600.0)

        created = await create_automatic_shorts(session)

    assert created == 0


async def test_the_run_is_bounded_by_the_title_limit(app: FastAPI) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        for index in range(3):
            media, _ = await _title(session, f"Title {index}")
            await _watch(session, users, media.id, hotspot_at=600.0)

        created = await create_automatic_shorts(session, title_limit=2)

    assert created == 2


async def test_the_clip_length_never_exceeds_what_the_schema_allows(app: FastAPI) -> None:
    """The planner and the check constraint have to agree on the ceiling."""

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        users = [await create_user(app, username=f"u{index}") for index in range(4)]
        media, _ = await _title(session)
        await _watch(session, users, media.id, hotspot_at=600.0)

        await create_automatic_shorts(
            session, options=HotspotOptions(short_seconds=300.0, long_seconds=300.0)
        )
        short = await session.scalar(select(Short))

    assert short is not None
    assert short.end_seconds - short.start_seconds <= 300.0
