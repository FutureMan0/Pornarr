"""Configured scene-metadata providers.

The adapters have existed since the metadata cascade was written, and nothing
ever stored a key for one, so every import fell back to the file name. This is
where an operator's key lives.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin
from pornarr_db.types import EncryptedString


class MetadataProvider(TimestampMixin, Base):
    __tablename__ = "metadata_providers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # One row per implementation: two StashDB keys would only be two answers to
    # the same question, and the cascade asks each provider once.
    implementation: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
