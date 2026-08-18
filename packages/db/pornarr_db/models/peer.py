"""Another Pornarr instance whose library this one is allowed to browse.

A peer is a credential plus a URL, not a copy of anybody's data: nothing from
the other side is stored here, which is the point — two households share what
they can see, not what they own. `api_key` is a key issued by the *remote*
instance and is encrypted at rest for the same reason an indexer key is: a
database handed to somebody else must not hand them the other server too.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin
from pornarr_db.types import EncryptedString


class Peer(TimestampMixin, Base):
    __tablename__ = "peers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    health: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    # A short code, never the exception text: an httpx error message can carry
    # the URL the key was sent to, and this column is returned to the browser.
    health_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the peer reported the last time it answered a test. A snapshot, not a
    # count this instance can verify, so it stays null until a test succeeds.
    media_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
