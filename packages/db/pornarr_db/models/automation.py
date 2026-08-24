"""Per-user bounds for automatic downloads."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class AutomationRule(TimestampMixin, Base):
    """One disabled-by-default automation policy for each user."""

    __tablename__ = "automation_rules"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    minimum_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    daily_download_limit_gb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    max_concurrent_jobs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    max_downloads_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    allowed_qualities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    blocked_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    blocked_performers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
