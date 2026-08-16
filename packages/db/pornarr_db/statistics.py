"""Record and read robust rolling performance averages."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.statistics import PerformanceMeasurement, PerformanceMetric

WINDOW_SIZE = 10
TRIM_AFTER_SAMPLES = 5


@dataclass(frozen=True)
class PerformanceSummary:
    value: float | None
    sample_count: int


async def record_measurement(
    session: AsyncSession, *, metric: PerformanceMetric, scope: str | None = None, value: float
) -> None:
    """Store one positive observation for a metric and optional protocol or method."""
    if value <= 0:
        raise ValueError("Performance measurements must be positive.")
    session.add(PerformanceMeasurement(metric=metric, scope=scope, value=value))


async def rolling_average(
    session: AsyncSession, *, metric: PerformanceMetric, scope: str | None = None
) -> PerformanceSummary:
    """Return the newest robust moving average and the sample count it uses."""
    scope_filter = (
        PerformanceMeasurement.scope.is_(None)
        if scope is None
        else PerformanceMeasurement.scope == scope
    )
    values = list(
        await session.scalars(
            select(PerformanceMeasurement.value)
            .where(PerformanceMeasurement.metric == metric, scope_filter)
            .order_by(PerformanceMeasurement.created_at.desc())
            .limit(WINDOW_SIZE)
        )
    )
    if not values:
        return PerformanceSummary(value=None, sample_count=0)
    sample_count = len(values)
    if len(values) >= TRIM_AFTER_SAMPLES:
        values = sorted(values)[1:-1]
    return PerformanceSummary(value=sum(values) / len(values), sample_count=sample_count)
