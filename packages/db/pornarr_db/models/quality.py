"""Ranked qualities and the profiles that allow them."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pornarr_db.base import Base, TimestampMixin

_BYTES_PER_MEGABYTE = 1_000_000


class QualityDefinition(TimestampMixin, Base):
    __tablename__ = "quality_definitions"
    __table_args__ = (
        CheckConstraint("weight >= 0", name="weight_not_negative"),
        CheckConstraint("minimum_size_mb_per_minute >= 0", name="minimum_size_not_negative"),
        CheckConstraint(
            "maximum_size_mb_per_minute >= minimum_size_mb_per_minute",
            name="size_bounds_ordered",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    resolution: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_size_mb_per_minute: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_size_mb_per_minute: Mapped[float] = mapped_column(Float, nullable=False)

    def is_size_mislabelled(self, *, size_bytes: int, duration_seconds: float) -> bool:
        """Return whether a file lies outside this quality's decimal-MB/minute bounds."""
        if duration_seconds <= 0:
            return True
        megabytes_per_minute = size_bytes / _BYTES_PER_MEGABYTE / (duration_seconds / 60)
        return (
            not self.minimum_size_mb_per_minute
            <= megabytes_per_minute
            <= (self.maximum_size_mb_per_minute)
        )


class QualityProfile(TimestampMixin, Base):
    __tablename__ = "quality_profiles"
    __table_args__ = (
        Index(
            "uq_quality_profiles_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    cutoff_quality_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    minimum_custom_format_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cutoff_quality: Mapped[QualityDefinition] = relationship(foreign_keys=[cutoff_quality_id])
    items: Mapped[list[QualityProfileItem]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="QualityProfileItem.position",
    )


class QualityProfileItem(Base):
    __tablename__ = "quality_profile_items"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_not_negative"),
        UniqueConstraint("profile_id", "position"),
    )

    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    quality_definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_definitions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[QualityProfile] = relationship(back_populates="items")
    quality_definition: Mapped[QualityDefinition] = relationship()
