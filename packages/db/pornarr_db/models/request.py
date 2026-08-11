"""User-facing request lifecycle records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pornarr_db.base import Base, TimestampMixin


class RequestStatus(StrEnum):
    SEARCHING = "searching"
    RESULTS_FOUND = "results_found"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    AVAILABLE = "available"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    MONITORING = "monitoring"
    CANCELLED = "cancelled"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Request(TimestampMixin, Base):
    __tablename__ = "requests"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    selected_release_guid: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status", values_callable=_enum_values), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    download_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("download_jobs.id", ondelete="SET NULL"), nullable=True
    )
    history: Mapped[list[RequestHistory]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestHistory.created_at"
    )


class RequestHistory(Base):
    __tablename__ = "request_history"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status", values_callable=_enum_values), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request: Mapped[Request] = relationship(back_populates="history")
