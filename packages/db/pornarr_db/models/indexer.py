"""Configured external search indexers and their aggregate statistics."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin
from pornarr_db.types import EncryptedString


class Indexer(TimestampMixin, Base):
    __tablename__ = "indexers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    implementation: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    categories: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    health: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    health_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rss_guid: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class IndexerStats(TimestampMixin, Base):
    __tablename__ = "indexer_stats"

    indexer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("indexers.id", ondelete="CASCADE"), primary_key=True
    )
    queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    average_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    grabs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
