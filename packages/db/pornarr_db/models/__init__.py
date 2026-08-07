"""Model modules.

Every model module must be imported here. Alembic autogenerate only sees what is
registered on the metadata, and a model that is never imported produces an empty
migration with no error — the failure is silent, which is why the import list is
explicit rather than a directory scan.
"""

from __future__ import annotations

from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Studio, Tag
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.media import Media, MediaFile, MediaFileHistory
from pornarr_db.models.oidc import OidcProvider
from pornarr_db.models.user import User, UserRole

__all__ = [
    "ContentFilterProfile",
    "ContentFilterRule",
    "FilterAction",
    "FilterProfileScope",
    "FilterRuleKind",
    "Media",
    "MediaFile",
    "MediaFileHistory",
    "MediaPerformer",
    "MediaTag",
    "OidcProvider",
    "Performer",
    "Studio",
    "Tag",
    "User",
    "UserRole",
]
