"""Turn a validated import trigger into a library record or a quarantine item.

Intake (`import_intake`) only decides whether a file is worth looking at, and
the trigger it leaves behind is `ready`. This module is what `ready` means:
steps 2 to 11 of `docs/pipelines/import.md` - fingerprint, duplicate check,
technical and scene metadata, filter evaluation, placement and publication.
Without it a completed download stopped at `ready` and never reached the
library.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.filters import (
    ContentCandidate,
    FilterAction,
    evaluate_filters,
    resolve_rules,
)
from pornarr_core.filters import FilterRule as CoreFilterRule
from pornarr_core.filters import FilterRuleKind as CoreFilterRuleKind
from pornarr_core.library_placement import Placement, place_file
from pornarr_core.matching import ParsedRelease, parse_release
from pornarr_db.models.download import ImportTrigger
from pornarr_db.models.filters import ContentFilterProfile, ContentFilterRule, FilterProfileScope
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.session import session_scope
from pornarr_integrations.metadata import MetadataCandidate, MetadataProviderAdapter
from pornarr_media.hashing import oshash
from pornarr_media.probe import MediaProbeError, ProbeResult, probe
from pornarr_shared.config import Settings, get_settings
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import TRANSCODE_QUEUE, enqueue_once, job
from pornarr_worker.jobs.metadata import MetadataSubject, resolve_metadata_cascade
from pornarr_worker.jobs.quarantine import (
    QuarantineReason,
    QuarantineReasonCode,
    quarantine_file,
)

READY = "ready"
IMPORTED = "imported"
QUARANTINED = "quarantined"
DUPLICATE = "duplicate"

Prober = Callable[[Path], ProbeResult]
Fingerprinter = Callable[[Path], str | None]


ARTWORK_JOB_NAME = "generate_artwork_job"


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """The terminal status of one import, and the media it produced."""

    status: str
    media_id: UUID | None = None
    media_path: str | None = None


async def import_ready_trigger(
    session: AsyncSession,
    trigger_id: UUID,
    settings: Settings,
    *,
    adapters: Sequence[MetadataProviderAdapter] = (),
    probe_file: Prober = probe,
    fingerprint: Fingerprinter = oshash,
) -> ImportOutcome:
    """Resolve one ready trigger into a library record, quarantine or failure."""

    trigger = await session.get(ImportTrigger, trigger_id)
    if trigger is None:
        return ImportOutcome("missing_trigger")
    if trigger.status != READY:
        return ImportOutcome(trigger.status)

    source = Path(trigger.source_path)
    if not source.is_file():
        _fail(
            trigger,
            "download_removed",
            f"The completed download at {str(source)!r} was removed before it could be imported.",
        )
        return ImportOutcome(trigger.status)

    file_hash = await asyncio.to_thread(fingerprint, source)
    if file_hash is not None and await _already_imported(session, file_hash):
        trigger.status = DUPLICATE
        trigger.error_code = "duplicate"
        trigger.error_detail = "A file with the same fingerprint is already in the library."
        return ImportOutcome(trigger.status)

    try:
        technical: ProbeResult | None = await asyncio.to_thread(probe_file, source)
    except MediaProbeError:
        technical = None

    resolution = await resolve_metadata_cascade(
        session,
        MetadataSubject.from_path(source, oshash=file_hash),
        adapters,
        import_trigger_id=trigger.id,
    )
    candidate = resolution.candidate
    decision = evaluate_filters(
        ContentCandidate(
            title=candidate.title,
            tags=frozenset(candidate.tags),
            performers=frozenset(candidate.performers),
            metadata_confidence=resolution.confidence,
            # A file ffprobe cannot read has no type anybody can name, which is
            # what the unknown-file-type rule exists to catch.
            file_type=None if technical is None else technical.container,
        ),
        await _global_filter_rules(session),
    )

    if decision.action is FilterAction.REJECT:
        _fail(trigger, "filter_rule", "A content filter rejected this download before placement.")
        return ImportOutcome(trigger.status)
    if decision.action is FilterAction.QUARANTINE or technical is None:
        await quarantine_file(
            session,
            source,
            settings.quarantine_path,
            [_quarantine_reason(decision.rule, technical)],
            extracted_metadata=_extracted_metadata(candidate, resolution.confidence),
            technical_details=_technical_details(technical),
        )
        trigger.status = QUARANTINED
        return ImportOutcome(trigger.status)

    parsed = parse_release(source.stem)
    placement = await asyncio.to_thread(
        place_file,
        source,
        await _library_root(session, settings),
        candidate.studio,
        candidate.title,
        None if candidate.release_date is None else candidate.release_date.isoformat(),
        quality=parsed.resolution,
        # Nothing seeds a usenet download, so its file is moved rather than
        # left behind beside a hardlink nobody will ever collect.
        move=_is_usenet(source, settings),
    )
    media = _record_media(
        session, placement, candidate, resolution.confidence, technical, parsed, file_hash
    )
    await session.flush()
    trigger.status = IMPORTED
    trigger.error_code = None
    trigger.error_detail = None
    return ImportOutcome(trigger.status, media.id, str(placement.path))


async def import_media(context: dict[str, Any], trigger_id: str) -> str:
    """ARQ entrypoint: import one ready trigger and announce what happened."""

    adapters = context.get("metadata_providers", ())
    if not isinstance(adapters, Sequence):
        raise TypeError("metadata_providers must be a sequence of provider adapters")
    redis = context["redis"]
    await publish_event(redis, "import.started", {"trigger_id": trigger_id})
    async with session_scope() as session:
        outcome = await import_ready_trigger(
            session, UUID(trigger_id), get_settings(), adapters=adapters
        )
    media_id = None if outcome.media_id is None else str(outcome.media_id)
    if media_id is not None and outcome.media_path is not None:
        # Step 10 of the pipeline. The library is a grid of posters, so an item
        # without one is a broken image on the screen it lands on.
        settings = get_settings()
        await enqueue_once(
            redis,
            ARTWORK_JOB_NAME,
            outcome.media_path,
            str(settings.thumbnail_path),
            media_id,
            queue=TRANSCODE_QUEUE,
        )
    await publish_event(
        redis,
        "import.completed",
        {"trigger_id": trigger_id, "status": outcome.status, "media_id": media_id},
    )
    if media_id is not None:
        await publish_event(redis, "media.available", {"media_id": media_id})
    return outcome.status


async def _already_imported(session: AsyncSession, file_hash: str) -> bool:
    return (
        await session.scalar(
            select(MediaFile.id).where(MediaFile.oshash == file_hash, MediaFile.is_active.is_(True))
        )
    ) is not None


async def _library_root(session: AsyncSession, settings: Settings) -> Path:
    """Place into the operator's own root folder, or the default library path."""

    folder = await session.scalar(
        select(RootFolder)
        .where(RootFolder.enabled.is_(True))
        .order_by(RootFolder.created_at, RootFolder.id)
    )
    return settings.library_path if folder is None else Path(folder.path)


async def _global_filter_rules(session: AsyncSession) -> tuple[CoreFilterRule, ...]:
    """An import belongs to the instance, so only the global profile applies."""

    profiles = await session.scalars(
        select(ContentFilterProfile)
        .options(selectinload(ContentFilterProfile.rules))
        .where(ContentFilterProfile.scope == FilterProfileScope.GLOBAL)
    )
    rules = [_core_rule(rule) for profile in profiles for rule in profile.rules]
    return resolve_rules(rules, ())


def _core_rule(rule: ContentFilterRule) -> CoreFilterRule:
    return CoreFilterRule(
        id=str(rule.id),
        kind=CoreFilterRuleKind(rule.kind.value),
        pattern=rule.pattern,
        action=FilterAction(rule.action.value),
        enabled=rule.enabled,
    )


def _record_media(
    session: AsyncSession,
    placement: Placement,
    candidate: MetadataCandidate,
    confidence: float,
    technical: ProbeResult,
    parsed: ParsedRelease,
    file_hash: str | None,
) -> Media:
    file_stat = placement.path.stat()
    media = Media(
        title=candidate.title,
        normalized_title=candidate.title.casefold(),
        studio=candidate.studio,
        release_date=candidate.release_date,
        confidence=confidence,
    )
    session.add(
        MediaFile(
            media=media,
            path=str(placement.path),
            size=file_stat.st_size,
            modified_at_ns=file_stat.st_mtime_ns,
            codecs=_codec_payload(technical),
            resolution=technical.resolution,
            duration_seconds=technical.duration,
            bitrate=technical.bitrate,
            streams=[dict(stream) for stream in technical.streams],
            container=technical.container,
            quality=parsed.resolution,
            oshash=file_hash,
        )
    )
    return media


def _codec_payload(technical: ProbeResult) -> dict[str, object]:
    """The shape `/api/media/{id}/playback-info` reads to decide direct play."""

    video = _stream(technical, "video")
    audio = _stream(technical, "audio")
    return {
        "container": technical.container,
        "video": {
            "codec": video.get("codec_name"),
            "profile": video.get("profile"),
            "level": video.get("level"),
        },
        "audio": {"codec": audio.get("codec_name")},
    }


def _stream(technical: ProbeResult, codec_type: str) -> dict[str, object]:
    return next(
        (stream for stream in technical.streams if stream.get("codec_type") == codec_type), {}
    )


def _quarantine_reason(
    rule: CoreFilterRule | None, technical: ProbeResult | None
) -> QuarantineReason:
    if technical is None:
        return QuarantineReason(
            code=QuarantineReasonCode.UNEXPECTED_FILE_TYPE,
            detail="The file could not be probed, so its contents are unknown.",
            evidence={},
        )
    return QuarantineReason(
        code=QuarantineReasonCode.FILTER_RULE,
        detail="A content filter asked for this download to be reviewed before it is imported.",
        evidence={} if rule is None else {"rule_id": rule.id, "pattern": rule.pattern},
    )


def _extracted_metadata(candidate: MetadataCandidate, confidence: float) -> dict[str, object]:
    return {
        "title": candidate.title,
        "studio": candidate.studio,
        "performers": list(candidate.performers),
        "confidence": confidence,
    }


def _technical_details(technical: ProbeResult | None) -> dict[str, object]:
    if technical is None:
        return {}
    return {
        "container": technical.container,
        "resolution": technical.resolution,
        "duration_seconds": technical.duration,
        "bitrate": technical.bitrate,
        "codecs": list(technical.codecs),
    }


def _is_usenet(source: Path, settings: Settings) -> bool:
    try:
        source.relative_to(settings.usenet_path)
    except ValueError:
        return False
    return True


def _fail(trigger: ImportTrigger, code: str, detail: str) -> None:
    trigger.status = "failed"
    trigger.error_code = code
    trigger.error_detail = detail


IMPORT_MEDIA_JOB = job(import_media)
