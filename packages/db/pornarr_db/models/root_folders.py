"""Configured library roots for the filesystem scanner."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class RootFolder(TimestampMixin, Base):
    __tablename__ = "root_folders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    free_space_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
