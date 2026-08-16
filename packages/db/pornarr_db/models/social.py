"""What the people on the server say to each other about the library.

Everything here is scoped to one household server. There is no federation and
no outbound traffic: ratings and comments are visible to everyone signed in,
which is what `docs/design/mockups` A6 states, while collections and the
send-a-title inbox are addressed to one person.

Authorship goes through `User.display_name` rather than the username. A guest
joining a friend's server picks the name they appear under, and that name is
the only identity the rest of the household sees.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin

MINIMUM_STARS = 1
MAXIMUM_STARS = 5
# The design calls shorts "vertical clips under a minute", and one minute is
# still the length they are cut at. The cap is five times that because a hot
# passage — as opposed to a hot moment — is worth keeping whole, and a clip is
# still a clip at five minutes. Beyond it the feed would become a second
# library.
MAXIMUM_SHORT_SECONDS = 300.0
# What an automatic cut reaches for unless the watched region is wide.
DEFAULT_SHORT_SECONDS = 60.0


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class CommentState(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    HIDDEN = "hidden"


class ShortSource(StrEnum):
    MARKER = "marker"
    MANUAL = "manual"
    # Cut from where the household actually watches, not from a scene boundary
    # or by hand. Kept distinct so an administrator can tell the automatic ones
    # apart and delete them wholesale if the feed goes wrong.
    HOTSPOT = "hotspot"


class CollectionVisibility(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"


class Rating(TimestampMixin, Base):
    """One person's score for one title, on the five-star scale the UI shows."""

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id"),
        CheckConstraint(
            f"stars BETWEEN {MINIMUM_STARS} AND {MAXIMUM_STARS}", name="stars_within_scale"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stars: Mapped[int] = mapped_column(Integer, nullable=False)


class Comment(TimestampMixin, Base):
    """One remark about a title.

    Deleting the author removes the comment: a household server has no need for
    tombstones, and an orphaned remark attributed to nobody is worse than none.
    """

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="body_not_blank"),
        Index("ix_comments_media_id_created_at", "media_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[CommentState] = mapped_column(
        Enum(CommentState, name="comment_state", values_callable=_enum_values),
        nullable=False,
        default=CommentState.OPEN,
        server_default=CommentState.OPEN.value,
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommentLike(TimestampMixin, Base):
    """A like is an identity, not a counter: it has to be revocable exactly once."""

    __tablename__ = "comment_likes"
    __table_args__ = (UniqueConstraint("comment_id", "user_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    comment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class CommentReport(TimestampMixin, Base):
    """A reader flags a comment for the administrator's moderation queue."""

    __tablename__ = "comment_reports"
    __table_args__ = (UniqueConstraint("comment_id", "reporter_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    comment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reporter_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Short(TimestampMixin, Base):
    """A vertical clip cut out of a title, addressed by offsets rather than a file.

    Storing offsets instead of a rendered file keeps a short free: the player
    seeks into the existing media, so a clip costs a row and no disk.
    """

    __tablename__ = "shorts"
    __table_args__ = (
        UniqueConstraint("media_id", "start_seconds"),
        CheckConstraint("start_seconds >= 0", name="start_not_negative"),
        CheckConstraint("end_seconds > start_seconds", name="end_after_start"),
        CheckConstraint(
            f"end_seconds - start_seconds <= {MAXIMUM_SHORT_SECONDS}", name="within_clip_length"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[ShortSource] = mapped_column(
        Enum(ShortSource, name="short_source", values_callable=_enum_values),
        nullable=False,
        default=ShortSource.MANUAL,
        server_default=ShortSource.MANUAL.value,
    )


class MediaSend(TimestampMixin, Base):
    """One person hands a title to another with a note — the "Sent to you" feed.

    Sending the same title twice replaces nothing and creates nothing: the
    unique constraint makes a second send a no-op the API reports as a conflict,
    so a nudge cannot become a way to flood someone's inbox.
    """

    __tablename__ = "media_sends"
    __table_args__ = (
        UniqueConstraint("sender_id", "recipient_id", "media_id"),
        CheckConstraint("sender_id <> recipient_id", name="no_self_send"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    sender_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Collection(TimestampMixin, Base):
    """A guest's own shelf. Private by default, because the design says so.

    "Guests can build playlists — private to each guest". Sharing is opt-in per
    collection rather than a server-wide switch, so one shared shelf never
    exposes the rest.
    """

    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[CollectionVisibility] = mapped_column(
        Enum(CollectionVisibility, name="collection_visibility", values_callable=_enum_values),
        nullable=False,
        default=CollectionVisibility.PRIVATE,
        server_default=CollectionVisibility.PRIVATE.value,
    )


class CollectionItem(TimestampMixin, Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_id", "media_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    collection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
