"""Cut shorts from where the household actually watches.

Runs nightly over the most-watched titles, turns their `progress` events into
a watch heatmap, and cuts a clip at each moment that stands out from the
watching around it. The reasoning about what "stands out" means lives in
`pornarr_core.hotspots`; this module is the part that talks to the database.

Two properties it has to keep:

- **It never touches a hand-made short.** Only clips it created itself are
  reconsidered, so an administrator's cut is never moved or deleted by a job
  that ran overnight.
- **It is idempotent.** Running twice on unchanged behaviour produces the same
  clips, because planning skips any interval overlapping one that already
  exists.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.hotspots import (
    DEFAULT_OPTIONS,
    HotspotOptions,
    ShortPlan,
    find_hotspots,
    has_enough_behaviour,
    plan_shorts,
    watch_histogram,
)
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent, UserEventType
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.social import Short, ShortSource
from pornarr_db.session import session_scope
from pornarr_shared.jobs import job

# How many titles one nightly run considers. The heatmap query is per title, so
# this bounds the job rather than the library.
DEFAULT_TITLE_LIMIT = 50


def _watched_titles(limit: int) -> Select[tuple[UUID | None, int, int]]:
    """The most-watched titles by sample volume and distinct viewers."""
    return (
        select(
            UserEvent.media_id,
            func.count().label("samples"),
            func.count(func.distinct(UserEvent.user_id)).label("viewers"),
        )
        .where(
            UserEvent.event_type == UserEventType.PROGRESS.value,
            UserEvent.media_id.is_not(None),
            UserEvent.value.is_not(None),
        )
        .group_by(UserEvent.media_id)
        .order_by(func.count().desc())
        .limit(limit)
    )


async def _positions_seconds(
    session: AsyncSession, media_id: UUID, duration_seconds: float
) -> list[float]:
    """Progress events carry a percentage; the heatmap needs seconds."""
    percentages = await session.scalars(
        select(UserEvent.value).where(
            UserEvent.media_id == media_id,
            UserEvent.event_type == UserEventType.PROGRESS.value,
            UserEvent.value.is_not(None),
        )
    )
    return [
        percentage * duration_seconds / 100
        for percentage in percentages
        if percentage is not None and 0 <= percentage <= 100
    ]


async def _snap_points(session: AsyncSession, media_file_id: UUID) -> list[float]:
    """Scene boundaries, so a cut can open on a shot change rather than inside one."""
    return list(
        await session.scalars(
            select(SceneMarker.start_seconds)
            .where(SceneMarker.media_file_id == media_file_id)
            .order_by(SceneMarker.start_seconds)
        )
    )


async def plan_for_title(
    session: AsyncSession,
    media_id: UUID,
    *,
    samples: int,
    viewers: int,
    options: HotspotOptions = DEFAULT_OPTIONS,
) -> tuple[ShortPlan, ...]:
    """Everything needed to decide one title's clips, without writing any."""
    if not has_enough_behaviour(samples, viewers, options=options):
        return ()
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None or not media_file.duration_seconds:
        return ()
    duration = media_file.duration_seconds
    histogram = watch_histogram(
        await _positions_seconds(session, media_id, duration), duration, options=options
    )
    existing = [
        (start, end)
        for start, end in (
            await session.execute(
                select(Short.start_seconds, Short.end_seconds).where(Short.media_id == media_id)
            )
        ).tuples()
    ]
    return plan_shorts(
        find_hotspots(histogram, options=options),
        duration,
        existing_intervals=existing,
        snap_points_seconds=await _snap_points(session, media_file.id),
        options=options,
    )


def _title_for(media: Media, plan: ShortPlan) -> str:
    minutes, seconds = divmod(int(plan.start_seconds), 60)
    return f"{media.title} — {minutes:d}:{seconds:02d}"[:512]


async def create_automatic_shorts(
    session: AsyncSession,
    *,
    title_limit: int = DEFAULT_TITLE_LIMIT,
    options: HotspotOptions = DEFAULT_OPTIONS,
) -> int:
    """Cut clips for every title with enough behaviour behind it."""
    created = 0
    for media_id, samples, viewers in (
        await session.execute(_watched_titles(title_limit))
    ).tuples():
        if media_id is None:
            continue
        plans = await plan_for_title(
            session, media_id, samples=samples, viewers=viewers, options=options
        )
        if not plans:
            continue
        media = await session.get(Media, media_id)
        if media is None:
            continue
        for plan in plans:
            session.add(
                Short(
                    media_id=media_id,
                    title=_title_for(media, plan),
                    start_seconds=plan.start_seconds,
                    end_seconds=plan.end_seconds,
                    source=ShortSource.HOTSPOT,
                )
            )
            created += 1
        await session.flush()
    return created


async def generate_automatic_shorts_job(_: dict[str, Any]) -> int:
    """Nightly entry point; returns how many clips this run added."""
    async with session_scope() as session:
        return await create_automatic_shorts(session)


AUTOMATIC_SHORTS_JOB = job(generate_automatic_shorts_job)
