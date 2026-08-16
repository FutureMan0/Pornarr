"""OpenID Connect provider configuration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin
from pornarr_db.types import EncryptedString


class OidcProvider(TimestampMixin, Base):
    __tablename__ = "oidc_providers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    client_secret: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    username_claim: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="preferred_username",
        server_default="preferred_username",
    )
    role_claim: Mapped[str] = mapped_column(
        String(128), nullable=False, default="groups", server_default="groups"
    )
    role_mapping: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    default_role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default="user"
    )
    required_claim: Mapped[str | None] = mapped_column(String(128), nullable=True)
    required_claim_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    discovery_document: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    discovery_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OidcIdentity(TimestampMixin, Base):
    __tablename__ = "oidc_identities"
    __table_args__ = (UniqueConstraint("provider_id", "subject"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("oidc_providers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
