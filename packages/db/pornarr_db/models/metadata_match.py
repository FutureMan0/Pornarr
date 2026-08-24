"""Traceable metadata-resolution attempts for each import."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class MetadataMatchLog(TimestampMixin, Base):
    """One provider attempt, including misses and rate limits, not just matches."""

    __tablename__ = "metadata_match_log"
    __table_args__ = (Index("ix_metadata_match_log_import_trigger_id", "import_trigger_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    import_trigger_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("import_triggers.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
