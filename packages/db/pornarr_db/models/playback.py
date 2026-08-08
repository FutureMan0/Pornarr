"""Per-user playback state and recommendation signals."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class PlaybackProgress(TimestampMixin, Base):
    __tablename__ = "playback_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id"),
        Index(
            "ix_playback_progress_user_id_completed_updated_at",
            "user_id",
            "completed",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    position_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserEvent(Base):
    __tablename__ = "user_events"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id", "event_type"),
        Index("ix_user_events_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
