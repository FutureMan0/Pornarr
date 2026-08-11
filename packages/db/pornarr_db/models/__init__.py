"""Model modules.

Every model module must be imported here. Alembic autogenerate only sees what is
registered on the metadata, and a model that is never imported produces an empty
migration with no error — the failure is silent, which is why the import list is
explicit rather than a directory scan.
"""

from __future__ import annotations

from pornarr_db.models.api_keys import UserApiKey
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.custom_formats import (
    CustomFormat,
    CustomFormatCondition,
    CustomFormatField,
    CustomFormatOperator,
)
from pornarr_db.models.download import BlockedRelease, DownloadHistory, DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Studio, Tag
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.media import Media, MediaFile, MediaFileHistory
from pornarr_db.models.notification import Notification, NotificationKind, NotificationPreference
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.models.playback import PlaybackProgress, UserEvent
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.settings import Setting
from pornarr_db.models.statistics import PerformanceMeasurement, PerformanceMetric
from pornarr_db.models.storage import DailyStorageUsage
from pornarr_db.models.user import User, UserRole

__all__ = [
    "AuditLog",
    "AutomationRule",
    "BlockedRelease",
    "ContentFilterProfile",
    "ContentFilterRule",
    "CustomFormat",
    "CustomFormatCondition",
    "CustomFormatField",
    "CustomFormatOperator",
    "DailyStorageUsage",
    "DownloadClient",
    "DownloadHistory",
    "DownloadJob",
    "FilterAction",
    "FilterProfileScope",
    "FilterRuleKind",
    "Indexer",
    "IndexerStats",
    "Media",
    "MediaFile",
    "MediaFileHistory",
    "MediaPerformer",
    "MediaTag",
    "Notification",
    "NotificationKind",
    "NotificationPreference",
    "OidcIdentity",
    "OidcProvider",
    "PerformanceMeasurement",
    "PerformanceMetric",
    "Performer",
    "PlaybackProgress",
    "QualityDefinition",
    "QualityProfile",
    "QualityProfileItem",
    "ReleaseCache",
    "Request",
    "RequestHistory",
    "RequestStatus",
    "RootFolder",
    "Setting",
    "Studio",
    "Tag",
    "User",
    "UserApiKey",
    "UserEvent",
    "UserRole",
]
