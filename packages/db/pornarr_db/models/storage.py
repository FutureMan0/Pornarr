"""Daily per-user download consumption."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class DailyStorageUsage(TimestampMixin, Base):
    """One UTC-day consumption counter for one user.

    The date is part of the primary key instead of a mutable reset timestamp.
    A new UTC date is therefore an empty budget by construction.
    """

    __tablename__ = "daily_storage_usage"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    downloaded_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
