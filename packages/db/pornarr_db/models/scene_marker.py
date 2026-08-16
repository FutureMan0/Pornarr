"""Detected scene boundaries for one media file.

Attached to the file rather than to the title: an upgrade replaces the file and
its cuts land at different timestamps, so the markers have to go with it. The
cascade means a replaced file takes its stale index with it instead of leaving
markers pointing into a video nobody has any more.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class SceneMarker(TimestampMixin, Base):
    """One shot, as a half-open interval in seconds from the start of the file."""

    __tablename__ = "scene_markers"
    __table_args__ = (
        UniqueConstraint("media_file_id", "ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_not_negative"),
        CheckConstraint("start_seconds >= 0", name="start_not_negative"),
        CheckConstraint("end_seconds > start_seconds", name="end_after_start"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    media_file_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
