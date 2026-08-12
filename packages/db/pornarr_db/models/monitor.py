"""Persisted user subscriptions for automatic release discovery."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class MonitorKind(StrEnum):
    PERFORMER = "performer"
    STUDIO = "studio"
    QUERY = "query"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Monitor(TimestampMixin, Base):
    """One user-owned release-discovery subscription.

    Separate optional foreign keys make target deletion safe: removing a
    performer or studio also removes its monitor instead of retaining a
    polymorphic identifier with no referent.
    """

    __tablename__ = "monitors"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'performer' AND performer_id IS NOT NULL AND studio_id IS NULL "
            "AND query IS NULL AND normalized_query IS NULL) OR "
            "(kind = 'studio' AND performer_id IS NULL AND studio_id IS NOT NULL "
            "AND query IS NULL AND normalized_query IS NULL) OR "
            "(kind = 'query' AND performer_id IS NULL AND studio_id IS NULL "
            "AND query IS NOT NULL AND normalized_query IS NOT NULL)",
            name="monitor_has_exactly_one_target",
        ),
        CheckConstraint("minimum_score >= 0", name="minimum_score_not_negative"),
        UniqueConstraint("user_id", "performer_id"),
        UniqueConstraint("user_id", "studio_id"),
        UniqueConstraint("user_id", "normalized_query"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MonitorKind] = mapped_column(
        Enum(MonitorKind, name="monitor_kind", values_callable=_enum_values), nullable=False
    )
    performer_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("performers.id", ondelete="CASCADE"), nullable=True
    )
    studio_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("studios.id", ondelete="CASCADE"), nullable=True
    )
    query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    normalized_query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("quality_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    minimum_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    last_match_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
