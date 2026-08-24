"""Measured performance samples used by download estimates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, Float, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base


class PerformanceMetric(StrEnum):
    DOWNLOAD_SPEED = "download_speed"
    POST_PROCESSING_SECONDS_PER_GIB = "post_processing_seconds_per_gib"
    IMPORT_SECONDS_PER_GIB = "import_seconds_per_gib"
    DISK_WRITE_SPEED = "disk_write_speed"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class PerformanceMeasurement(Base):
    """One observed performance value, read through a bounded moving window."""

    __tablename__ = "performance_measurements"
    __table_args__ = (
        Index(
            "ix_performance_measurements_metric_scope_created_at", "metric", "scope", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    metric: Mapped[PerformanceMetric] = mapped_column(
        Enum(PerformanceMetric, name="performance_metric", values_callable=_enum_values),
        nullable=False,
    )
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
