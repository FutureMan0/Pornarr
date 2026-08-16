"""Who is allowed to see which titles.

One module, because the rule has to be identical in the library, in search, in
the feed and in the shorts feed. A guest who cannot see a title in the library
but can reach it through search has no privacy at all, and that kind of gap is
made by writing the same filter four times.

Two switches, and they do different jobs:

- `private_libraries` scopes **the library** to your own titles.
- `pooled_search` decides whether **search** still reaches the others. Pooled
  search is the point of a household server — you want to find out that a
  housemate already has the film — so it stays on by default, and the response
  says `in_my_library` without ever naming whose library the other copy is in.

An administrator sees the unfiltered pool. That is administration of the
library, not of what a guest saved: collections and the watchlist stay private
to their owner regardless of role.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from pornarr_db.models.media import Media
from pornarr_db.models.user import User, UserRole
from pornarr_db.settings import RuntimeSettings


def library_scope(user: User, settings: RuntimeSettings) -> ColumnElement[bool] | None:
    """The predicate limiting a library listing, or None for the whole pool."""
    if not settings.private_libraries or user.role is UserRole.ADMIN:
        return None
    # Unowned titles are the shared pool every server starts with; scoping must
    # not make a library that predates this feature disappear.
    return or_(Media.owner_id == user.id, Media.owner_id.is_(None))


def search_scope(user: User, settings: RuntimeSettings) -> ColumnElement[bool] | None:
    """The predicate limiting local search.

    With pooled search on this is deliberately wider than `library_scope`: the
    hits are returned, flagged with whether they are already yours.
    """
    if settings.pooled_search:
        return None
    return library_scope(user, settings)


def owns(media: Media, user: User) -> bool:
    """Whether a title counts as being in this person's own library."""
    return media.owner_id is None or media.owner_id == user.id
