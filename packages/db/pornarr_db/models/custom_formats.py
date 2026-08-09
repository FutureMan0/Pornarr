"""User-configured scoring rules for release properties."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pornarr_db.base import Base, TimestampMixin


class CustomFormatField(StrEnum):
    TITLE = "title"
    CODEC = "codec"
    SOURCE = "source"
    SIZE = "size"
    INDEXER = "indexer"
    PROTOCOL = "protocol"
    FLAGS = "flags"


class CustomFormatOperator(StrEnum):
    EQUALS = "equals"
    CONTAINS = "contains"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class CustomFormat(TimestampMixin, Base):
    __tablename__ = "custom_formats"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conditions: Mapped[list[CustomFormatCondition]] = relationship(
        back_populates="custom_format", cascade="all, delete-orphan"
    )


class CustomFormatCondition(TimestampMixin, Base):
    __tablename__ = "custom_format_conditions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    custom_format_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("custom_formats.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[CustomFormatField] = mapped_column(
        Enum(CustomFormatField, name="custom_format_field", values_callable=_enum_values),
        nullable=False,
    )
    operator: Mapped[CustomFormatOperator] = mapped_column(
        Enum(CustomFormatOperator, name="custom_format_operator", values_callable=_enum_values),
        nullable=False,
    )
    value: Mapped[object] = mapped_column(JSON, nullable=False)
    negate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    custom_format: Mapped[CustomFormat] = relationship(back_populates="conditions")
