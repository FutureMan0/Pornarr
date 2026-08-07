"""Performers, studios, tags and their media assignments."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class Performer(TimestampMixin, Base):
    __tablename__ = "performers"
    __table_args__ = (
        Index(
            "ix_performers_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class Studio(TimestampMixin, Base):
    __tablename__ = "studios"
    __table_args__ = (
        Index(
            "ix_studios_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class Tag(TimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (
        Index(
            "ix_tags_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class MediaPerformer(Base):
    __tablename__ = "media_performers"
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    performer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("performers.id", ondelete="CASCADE"), primary_key=True
    )


class MediaTag(TimestampMixin, Base):
    __tablename__ = "media_tags"
    __table_args__ = (Index("uq_media_tags_source", "media_id", "tag_id", "source", unique=True),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
