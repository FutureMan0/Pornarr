"""Persistent download queue, terminal history and temporary release blocks."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class DownloadJob(TimestampMixin, Base):
    """A current download-client job.

    ``release_guid`` is the indexer's canonical external identity. It keeps
    idempotency and retry decisions independent from a release cache entry's
    lifetime. Client identity is deliberately snapshotted so a deleted client
    does not make an existing job unintelligible.
    """

    __tablename__ = "download_jobs"
    __table_args__ = (
        Index("ix_download_jobs_queue", "status", "priority"),
        UniqueConstraint("download_client_id", "client_job_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    download_client_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("download_clients.id", ondelete="SET NULL"), nullable=True
    )
    client_name: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    release_guid: Mapped[str] = mapped_column(String(1024), nullable=False)
    client_job_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remaining_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    download_speed_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DownloadHistory(Base):
    """An immutable terminal outcome, retained after its current job is removed."""

    __tablename__ = "download_history"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    download_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("download_jobs.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    client_name: Mapped[str] = mapped_column(String(128), nullable=False)
    client_job_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    release_guid: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImportTrigger(TimestampMixin, Base):
    """One durable handover from a completed download into the import pipeline."""

    __tablename__ = "import_triggers"
    __table_args__ = (Index("ix_import_triggers_status", "status"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    download_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("download_jobs.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    source_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    reported_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class BlockedRelease(TimestampMixin, Base):
    """A release that must not be retried until ``blocked_until`` has passed."""

    __tablename__ = "blocked_releases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    release_guid: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    blocked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
