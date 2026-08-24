"""Incremental, per-user recommendation preferences."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base


class PreferenceAxis(StrEnum):
    TAG = "tag"
    PERFORMER = "performer"
    STUDIO = "studio"
    QUALITY = "quality"


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (Index("ix_user_preferences_user_id_axis_score", "user_id", "axis", "score"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    axis: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(256), primary_key=True)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserPreferenceState(Base):
    __tablename__ = "user_preference_states"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_event_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
