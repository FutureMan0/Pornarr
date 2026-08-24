"""Operator-configured content-filter profiles and rules."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pornarr_db.base import Base, TimestampMixin


class FilterProfileScope(StrEnum):
    GLOBAL = "global"
    USER = "user"


class FilterRuleKind(StrEnum):
    TERM = "term"
    TAG = "tag"
    PERFORMER = "performer"
    MINIMUM_CONFIDENCE = "minimum_confidence"
    UNKNOWN_PERFORMER_AGE = "unknown_performer_age"
    UNKNOWN_FILE_TYPE = "unknown_file_type"


class FilterAction(StrEnum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    REJECT = "reject"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class ContentFilterProfile(TimestampMixin, Base):
    __tablename__ = "content_filter_profiles"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'global' AND user_id IS NULL) OR (scope = 'user' AND user_id IS NOT NULL)",
            name="scope_owner",
        ),
        Index(
            "uq_content_filter_profiles_global_scope",
            "scope",
            unique=True,
            postgresql_where=text("scope = 'global'"),
            sqlite_where=text("scope = 'global'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    scope: Mapped[FilterProfileScope] = mapped_column(
        Enum(FilterProfileScope, name="filter_profile_scope", values_callable=_enum_values),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )
    rules: Mapped[list[ContentFilterRule]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ContentFilterRule(TimestampMixin, Base):
    __tablename__ = "content_filter_rules"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("content_filter_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[FilterRuleKind] = mapped_column(
        Enum(FilterRuleKind, name="filter_rule_kind", values_callable=_enum_values), nullable=False
    )
    pattern: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    action: Mapped[FilterAction] = mapped_column(
        Enum(FilterAction, name="filter_action", values_callable=_enum_values),
        nullable=False,
        default=FilterAction.REJECT,
        server_default=FilterAction.REJECT.value,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    profile: Mapped[ContentFilterProfile] = relationship(back_populates="rules")
