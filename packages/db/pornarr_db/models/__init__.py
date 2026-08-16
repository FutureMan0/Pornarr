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
from pornarr_db.models.download import BlockedRelease, DownloadHistory, DownloadJob, ImportTrigger
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
from pornarr_db.models.media import DuplicateCandidate, Media, MediaFile, MediaFileHistory
from pornarr_db.models.metadata_correction import MetadataCorrection
from pornarr_db.models.metadata_match import MetadataMatchLog
from pornarr_db.models.monitor import Monitor, MonitorKind
from pornarr_db.models.notification import Notification, NotificationKind, NotificationPreference
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.models.playback import PlaybackProgress, UserEvent, UserEventType
from pornarr_db.models.preferences import PreferenceAxis, UserPreference, UserPreferenceState
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.quarantine import QuarantineItem
from pornarr_db.models.recommendation import RecommendationCandidate
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.settings import Setting
from pornarr_db.models.social import (
    Collection,
    CollectionItem,
    CollectionVisibility,
    Comment,
    CommentLike,
    CommentReport,
    CommentState,
    MediaSend,
    Rating,
    Short,
    ShortSource,
)
from pornarr_db.models.statistics import PerformanceMeasurement, PerformanceMetric
from pornarr_db.models.storage import DailyStorageUsage
from pornarr_db.models.user import User, UserRole

__all__ = [
    "AuditLog",
    "AutomationRule",
    "BlockedRelease",
    "Collection",
    "CollectionItem",
    "CollectionVisibility",
    "Comment",
    "CommentLike",
    "CommentReport",
    "CommentState",
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
    "DuplicateCandidate",
    "FilterAction",
    "FilterProfileScope",
    "FilterRuleKind",
    "ImportTrigger",
    "Indexer",
    "IndexerStats",
    "Media",
    "MediaFile",
    "MediaFileHistory",
    "MediaPerformer",
    "MediaSend",
    "MediaTag",
    "MetadataCorrection",
    "MetadataMatchLog",
    "Monitor",
    "MonitorKind",
    "Notification",
    "NotificationKind",
    "NotificationPreference",
    "OidcIdentity",
    "OidcProvider",
    "PerformanceMeasurement",
    "PerformanceMetric",
    "Performer",
    "PlaybackProgress",
    "PreferenceAxis",
    "QualityDefinition",
    "QualityProfile",
    "QualityProfileItem",
    "QuarantineItem",
    "Rating",
    "RecommendationCandidate",
    "ReleaseCache",
    "Request",
    "RequestHistory",
    "RequestStatus",
    "RootFolder",
    "SceneMarker",
    "Setting",
    "Short",
    "ShortSource",
    "Studio",
    "Tag",
    "User",
    "UserApiKey",
    "UserEvent",
    "UserEventType",
    "UserPreference",
    "UserPreferenceState",
    "UserRole",
]
