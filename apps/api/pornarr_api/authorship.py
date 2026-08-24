"""How a person is named to everyone else on the server.

One place, because the rule has to be identical on ratings, comments and sends:
a mismatch would let a guest be pseudonymous in one list and identifiable in
the next, which defeats the point of choosing a display name at all.
"""

from __future__ import annotations

from pornarr_db.models.user import User


def author_name(user: User) -> str:
    """The name the household sees, never the credential.

    `username` is what someone signs in with. Falling back to it when no display
    name is set keeps every author labelled, and a blank display name is treated
    as unset so an all-whitespace value cannot produce a nameless author.
    """
    display_name = (user.display_name or "").strip()
    return display_name or user.username
