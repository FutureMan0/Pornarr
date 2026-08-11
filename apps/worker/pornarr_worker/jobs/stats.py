"""Record measurements produced by completed download and import work."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.statistics import record_measurement

GIB = 1024**3


async def record_download_speed(
    session: AsyncSession, *, protocol: str, size_bytes: int, duration_seconds: float
) -> None:
    await record_measurement(
        session,
        metric=PerformanceMetric.DOWNLOAD_SPEED,
        scope=protocol,
        value=_rate(size_bytes, duration_seconds),
    )


async def record_post_processing(
    session: AsyncSession, *, size_bytes: int, duration_seconds: float
) -> None:
    await record_measurement(
        session,
        metric=PerformanceMetric.POST_PROCESSING_SECONDS_PER_GIB,
        scope="usenet",
        value=_seconds_per_gib(size_bytes, duration_seconds),
    )


async def record_import_duration(
    session: AsyncSession, *, method: str, size_bytes: int, duration_seconds: float
) -> None:
    await record_measurement(
        session,
        metric=PerformanceMetric.IMPORT_SECONDS_PER_GIB,
        scope=method,
        value=_seconds_per_gib(size_bytes, duration_seconds),
    )


async def record_disk_write_speed(
    session: AsyncSession, *, size_bytes: int, duration_seconds: float
) -> None:
    await record_measurement(
        session,
        metric=PerformanceMetric.DISK_WRITE_SPEED,
        value=_rate(size_bytes, duration_seconds),
    )


def _rate(size_bytes: int, duration_seconds: float) -> float:
    if size_bytes <= 0 or duration_seconds <= 0:
        raise ValueError("Measurements need positive size and duration.")
    return size_bytes / duration_seconds


def _seconds_per_gib(size_bytes: int, duration_seconds: float) -> float:
    if size_bytes <= 0 or duration_seconds <= 0:
        raise ValueError("Measurements need positive size and duration.")
    return duration_seconds / (size_bytes / GIB)
