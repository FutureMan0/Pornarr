"""Files withheld from the library pending human review."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class QuarantineItem(TimestampMixin, Base):
    """A moved source file and every concrete reason it needs review."""

    __tablename__ = "quarantine_items"
    __table_args__ = (Index("ix_quarantine_items_created_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    original_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    quarantine_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    extracted_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    technical_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
