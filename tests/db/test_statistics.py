"""Rolling performance statistics."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_db.base import Base
from pornarr_db.statistics import PerformanceMetric, record_measurement, rolling_average


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


async def test_rolling_average_discards_one_outlier_after_ten_samples() -> None:
    async for session in _session():
        for _ in range(9):
            await record_measurement(
                session,
                metric=PerformanceMetric.DOWNLOAD_SPEED,
                scope="usenet",
                value=100,
            )
        await record_measurement(
            session,
            metric=PerformanceMetric.DOWNLOAD_SPEED,
            scope="usenet",
            value=10_000,
        )
        await session.commit()

        summary = await rolling_average(
            session, metric=PerformanceMetric.DOWNLOAD_SPEED, scope="usenet"
        )

    assert summary.sample_count == 10
    assert summary.value == 100


async def test_rolling_average_keeps_measurement_dimensions_separate() -> None:
    async for session in _session():
        await record_measurement(
            session,
            metric=PerformanceMetric.DOWNLOAD_SPEED,
            scope="torrent",
            value=50,
        )
        await record_measurement(
            session,
            metric=PerformanceMetric.IMPORT_SECONDS_PER_GIB,
            scope="copy",
            value=20,
        )
        await session.commit()

        torrent = await rolling_average(
            session, metric=PerformanceMetric.DOWNLOAD_SPEED, scope="torrent"
        )
        copy = await rolling_average(
            session, metric=PerformanceMetric.IMPORT_SECONDS_PER_GIB, scope="copy"
        )

    assert (torrent.value, copy.value) == (50, 20)
