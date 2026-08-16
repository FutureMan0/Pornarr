"""Operator corrections that make later metadata resolution deterministic."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class MetadataCorrection(TimestampMixin, Base):
    """One exact normalised source title with its administrator-approved metadata."""

    __tablename__ = "metadata_corrections"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_title: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    studio: Mapped[str | None] = mapped_column(String(256), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
