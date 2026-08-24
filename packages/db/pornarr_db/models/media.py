"""Logical media records, their physical files and replacement history."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pornarr_db.base import Base, TimestampMixin


class Media(TimestampMixin, Base):
    __tablename__ = "media"
    __table_args__ = (
        Index(
            "ix_media_normalized_title_trgm",
            "normalized_title",
            postgresql_using="gin",
            postgresql_ops={"normalized_title": "gin_trgm_ops"},
        ),
        Index(
            "ix_media_normalized_title_trgm_knn",
            "normalized_title",
            postgresql_using="gist",
            postgresql_ops={"normalized_title": "gist_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Whose library this title belongs to. Null means the shared pool, which is
    # what every existing row is and what a server with `private_libraries` off
    # keeps producing — the column is a scope, not an owner in the legal sense.
    # `SET NULL` rather than cascade: deleting a guest must not delete media.
    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    studio: Mapped[str | None] = mapped_column(String(256), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="available", server_default="available"
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    files: Mapped[list[MediaFile]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )


class MediaFile(TimestampMixin, Base):
    __tablename__ = "media_files"
    __table_args__ = (
        Index(
            "uq_media_files_active_media",
            "media_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    modified_at_ns: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    codecs: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bitrate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    streams: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    container: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    custom_format_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    oshash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_missing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    media: Mapped[Media] = relationship(back_populates="files")


class MediaFileHistory(Base):
    __tablename__ = "media_file_history"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    replaced_file_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    replacement_file_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DuplicateCandidate(TimestampMixin, Base):
    """A non-destructive perceptual match awaiting human review."""

    __tablename__ = "duplicate_candidates"
    __table_args__ = (UniqueConstraint("media_file_id", "candidate_file_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    media_file_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    candidate_file_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    hamming_distance: Mapped[int] = mapped_column(Integer, nullable=False)
