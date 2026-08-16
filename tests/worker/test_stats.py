"""Measurement recorders used by completed download and import jobs."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.statistics import rolling_average
from pornarr_worker.jobs.stats import (
    GIB,
    record_disk_write_speed,
    record_download_speed,
    record_import_duration,
    record_post_processing,
)


async def _session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def test_recorders_normalise_completed_work_by_size() -> None:
    async for session in _session():
        await record_download_speed(
            session, protocol="usenet", size_bytes=2 * GIB, duration_seconds=2
        )
        await record_post_processing(session, size_bytes=2 * GIB, duration_seconds=20)
        await record_import_duration(
            session, method="copy", size_bytes=2 * GIB, duration_seconds=10
        )
        await record_disk_write_speed(session, size_bytes=2 * GIB, duration_seconds=4)
        await session.commit()

        download = await rolling_average(
            session, metric=PerformanceMetric.DOWNLOAD_SPEED, scope="usenet"
        )
        post_processing = await rolling_average(
            session, metric=PerformanceMetric.POST_PROCESSING_SECONDS_PER_GIB, scope="usenet"
        )
        imported = await rolling_average(
            session, metric=PerformanceMetric.IMPORT_SECONDS_PER_GIB, scope="copy"
        )
        disk = await rolling_average(session, metric=PerformanceMetric.DISK_WRITE_SPEED)

    assert (download.value, post_processing.value, imported.value, disk.value) == (
        float(GIB),
        10,
        5,
        GIB / 2,
    )


@pytest.mark.parametrize(("size_bytes", "duration_seconds"), [(0, 1), (1, 0)])
async def test_recorders_reject_non_positive_measurements(
    size_bytes: int, duration_seconds: float
) -> None:
    async for session in _session():
        with pytest.raises(ValueError, match="positive size and duration"):
            await record_download_speed(
                session,
                protocol="torrent",
                size_bytes=size_bytes,
                duration_seconds=duration_seconds,
            )
