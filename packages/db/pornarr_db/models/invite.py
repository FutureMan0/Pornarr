"""An invitation to join this server.

WHAT IS STORED IS A HASH, NOT THE LINK. The token lives in the URL an
administrator copies and nowhere else: a database dump, a backup or a stray log
line must not be enough to join a household. The row holds only what is needed
to recognise a token that is presented, which is the same reasoning the password
column follows.

SINGLE USE, BY RECORD RATHER THAN BY DELETION. `redeemed_at` and `redeemed_by`
stay after the invitation is used, so an administrator can see who joined
through which link. Deleting the row on redemption would lose exactly the fact
somebody would want later — who let this person in.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class Invite(TimestampMixin, Base):
    __tablename__ = "invites"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # A SHA-256 of the token. Unique so a presented token is one index lookup
    # rather than a scan comparing every row.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # An invitation with no end is a permanent way in that nobody remembers
    # issuing, so this is not nullable.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # What an administrator wrote to remember who it was for. Never shown to the
    # person joining — "for Lea's iPad" is a note to self, not a greeting.
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)
