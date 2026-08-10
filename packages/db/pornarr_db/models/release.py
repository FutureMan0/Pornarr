"""Normalized, expiring releases discovered from external indexers."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin
from pornarr_db.types import EncryptedString


class ReleaseCache(TimestampMixin, Base):
    __tablename__ = "release_cache"
    __table_args__ = (
        Index("uq_release_cache_indexer_guid", "indexer_id", "guid", unique=True),
        Index("ix_release_cache_indexer_expires_at", "indexer_id", "expires_at"),
        Index(
            "ix_release_cache_normalized_title_trgm",
            "normalized_title",
            postgresql_using="gin",
            postgresql_ops={"normalized_title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    indexer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("indexers.id", ondelete="CASCADE"), nullable=False
    )
    guid: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(1024), nullable=False)
    details_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_url: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    seeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    info_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    magnet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    groups: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    poster: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    password_protected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
