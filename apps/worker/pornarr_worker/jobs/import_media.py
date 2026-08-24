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

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.dedup import DuplicateClassification, MediaCandidate, detect_duplicate
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
from pornarr_core.naming import normalize_title
from pornarr_core.quality import (
    ExistingFile,
    QualityDecision,
    QualityVerdict,
    ReleaseCandidate,
    decide_quality,
)
from pornarr_core.quality import QualityProfile as CoreQualityProfile
from pornarr_db.audit import write_audit
from pornarr_db.models.download import ImportTrigger
from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer, Tag
from pornarr_db.models.filters import ContentFilterProfile, ContentFilterRule, FilterProfileScope
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.request import Request
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.session import session_scope
from pornarr_integrations.metadata import MetadataCandidate, MetadataProviderAdapter
from pornarr_media.hashing import oshash
from pornarr_media.probe import MediaProbeError, ProbeResult, codec_payload, probe
from pornarr_shared.audit import AuditSource
from pornarr_shared.config import Settings, get_settings
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import TRANSCODE_QUEUE, enqueue_once, job
from pornarr_worker.jobs.metadata import MetadataSubject, resolve_metadata_cascade
from pornarr_worker.jobs.quarantine import (
    QuarantineReason,
    QuarantineReasonCode,
    quarantine_file,
)
from pornarr_worker.jobs.upgrade import upgrade_media_file
from pornarr_worker.metadata_providers import configured_providers

READY = "ready"
IMPORTED = "imported"
QUARANTINED = "quarantined"
DUPLICATE = "duplicate"

Prober = Callable[[Path], ProbeResult]
Fingerprinter = Callable[[Path], str | None]


ARTWORK_JOB_NAME = "generate_artwork_job"
SPRITE_JOB_NAME = "generate_preview_sprite_job"


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
        await _filter_rules(session, trigger),
    )
    # Written before the branch, so the record exists whichever way the decision
    # goes and a rule that only ever quarantines is not invisible next to one
    # that rejects. docs/pipelines/import.md L31. No actor: nobody asked for
    # this, the pipeline reached it on its own.
    for fired in decision.matched:
        write_audit(
            session,
            actor_id=None,
            source=AuditSource.AUTOMATION,
            action="filter.matched",
            target=str(fired.id),
            # The rule's identity and what it asked for, never its pattern: a
            # term rule's pattern is the content vocabulary an operator chose to
            # keep out, and an audit log is read by more people than configure
            # one.
            context={
                "kind": fired.kind.value,
                "rule_action": fired.action.value,
                "outcome": decision.action.value,
                "import_trigger_id": str(trigger.id),
            },
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

    duplicate = await _fuzzy_duplicate(session, candidate, technical)
    # Step 7, and it reads the duplicate check's answer because two of the three
    # verdicts docs/pipelines/import.md:26-27 names - not an upgrade, and a held
    # file already past the cutoff - are about the file this import would
    # replace, which is exactly what step 3's fuzzy half identifies.
    rejection = await _quality_rejection(session, parsed, duplicate)
    if rejection is not None:
        _fail(
            trigger,
            f"quality_{rejection.reason.value}",
            f"The quality profile refused this release: {rejection.reason.value}.",
        )
        return ImportOutcome(trigger.status)
    if duplicate is not None:
        # Step 3's fuzzy half: title, studio, date and duration all cleared the
        # threshold, so this is a better file for a title already in the
        # library, not a second one. Adopt the newer scrape's metadata, then
        # replace the active file the same way an explicit upgrade would -
        # verified before the old file is ever touched.
        _adopt_candidate(duplicate, candidate, resolution.confidence)
        upgraded = await upgrade_media_file(
            session,
            duplicate.id,
            source,
            await _library_root(session, settings),
            quality=parsed.resolution,
            probe_file=probe_file,
        )
        trigger.status = IMPORTED
        trigger.error_code = None
        trigger.error_detail = None
        return ImportOutcome(trigger.status, duplicate.id, str(upgraded.path))

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
    await _record_scene_vocabulary(
        session, media, candidate, resolution.confidence, resolution.provider
    )
    trigger.status = IMPORTED
    trigger.error_code = None
    trigger.error_detail = None
    return ImportOutcome(trigger.status, media.id, str(placement.path))


async def import_media(context: dict[str, Any], trigger_id: str) -> str:
    """ARQ entrypoint: import one ready trigger and announce what happened."""

    redis = context["redis"]
    await publish_event(redis, "import.started", {"trigger_id": trigger_id})
    async with session_scope() as session:
        outcome = await import_ready_trigger(
            session,
            UUID(trigger_id),
            get_settings(),
            adapters=await configured_providers(session),
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
        # Same step: the library grid also swaps a poster for a hover preview
        # on the fly, and nothing produced the sprite or its VTT index for it
        # to swap in.
        await enqueue_once(
            redis,
            SPRITE_JOB_NAME,
            outcome.media_path,
            str(settings.thumbnail_path / media_id),
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


async def _filter_rules(
    session: AsyncSession, trigger: ImportTrigger
) -> tuple[CoreFilterRule, ...]:
    """The global profile, tightened by the profile of whoever asked for the file.

    docs/pipelines/import.md:29-30 asks step 8 for "global and user filter rules,
    resolved with the user profile only ever tightening the global one", and
    `resolve_rules` already keeps global precedence on a tie. The open question
    was whose user profile that is. An import belongs to the instance, so the
    global profile always applies; the user half is the account whose request
    produced the download, which the trigger reaches through the download job a
    `Request` was attached to. Only that account's, and only when there is one:
    a file nobody requested - a library scan, an operator dropping a release
    into the completed directory - is tightened by nothing, because a rule one
    reader wrote about their own view must never decide what the instance keeps
    on disk for everybody else.
    """

    requester = await _requesting_user(session, trigger)
    owned_by_requester = ContentFilterProfile.scope == FilterProfileScope.GLOBAL
    if requester is not None:
        owned_by_requester = or_(owned_by_requester, ContentFilterProfile.user_id == requester)
    profiles = await session.scalars(
        select(ContentFilterProfile)
        .options(selectinload(ContentFilterProfile.rules))
        .where(owned_by_requester)
    )
    global_rules: list[CoreFilterRule] = []
    user_rules: list[CoreFilterRule] = []
    for profile in profiles:
        target = global_rules if profile.scope is FilterProfileScope.GLOBAL else user_rules
        target.extend(_core_rule(rule) for rule in profile.rules)
    return resolve_rules(global_rules, user_rules)


async def _requesting_user(session: AsyncSession, trigger: ImportTrigger) -> UUID | None:
    """The account whose request this download fulfils, if it fulfils one.

    `Request.target_owner_id` names whose library the result lands in, which is
    a placement question; the filter profile that may tighten this import is the
    one belonging to the reader who asked, and that is `Request.user_id`.
    """

    if trigger.download_job_id is None:
        return None
    return await session.scalar(
        select(Request.user_id)
        .where(Request.download_job_id == trigger.download_job_id)
        .order_by(Request.created_at, Request.id)
        .limit(1)
    )


async def _quality_rejection(
    session: AsyncSession, parsed: ParsedRelease, upgrade_of: Media | None
) -> QualityDecision | None:
    """Step 7: the quality verdict, or nothing if there is no verdict to give.

    docs/pipelines/import.md:26-27 ends the import here when the release is not
    in the profile, is not an upgrade on what is held, or would improve on a
    file that already reaches the cutoff - the half of ADR 0029 that was never
    wired into the import path, so a release outside every profile was imported
    anyway. Two situations produce no verdict rather than a rejection: an
    instance with no default profile has not expressed an opinion yet, and a
    release whose resolution matches no ranked definition cannot be scored at
    all. The file is already on disk in both cases, and refusing what nobody
    ranked would throw away a download for the absence of configuration.
    """

    profile = await session.scalar(
        select(QualityProfile)
        .options(
            selectinload(QualityProfile.cutoff_quality),
            selectinload(QualityProfile.items).selectinload(QualityProfileItem.quality_definition),
        )
        .where(QualityProfile.is_default.is_(True))
    )
    if profile is None:
        return None
    quality = await _quality_definition(session, parsed.resolution)
    if quality is None:
        return None
    decision = decide_quality(
        ReleaseCandidate(quality=quality.name, quality_rank=quality.weight),
        CoreQualityProfile(
            allowed_qualities=frozenset(item.quality_definition.name for item in profile.items),
            cutoff_quality_rank=profile.cutoff_quality.weight,
            minimum_custom_format_score=profile.minimum_custom_format_score,
        ),
        existing=await _existing_file(session, upgrade_of),
    )
    return decision if decision.verdict is QualityVerdict.REJECT else None


async def _quality_definition(
    session: AsyncSession, resolution: str | None
) -> QualityDefinition | None:
    """The ranked definition a resolution names, lowest rank where several share it.

    An import reads its quality out of the file name, which states a resolution
    far more reliably than it states a source. Where two definitions rank the
    same resolution the lower one is taken: a release that does not say where it
    came from must not be credited with the better-ranked variant, because that
    rank is what decides whether it counts as an upgrade on the held file.
    """

    if resolution is None:
        return None
    return await session.scalar(
        select(QualityDefinition)
        .where(func.lower(QualityDefinition.resolution) == resolution.casefold())
        .order_by(QualityDefinition.weight, QualityDefinition.name)
        .limit(1)
    )


async def _existing_file(session: AsyncSession, media: Media | None) -> ExistingFile | None:
    """The rank of the active file this import would replace, if there is one.

    `decide_quality`'s "not an upgrade" and "cutoff met" verdicts are both about
    a file already on disk, and the only one this release can be measured
    against is the active file of the media step 3 named as its upgrade
    candidate. A title the library does not hold has nothing to improve on, and
    neither has one whose held file records a quality no definition ranks.
    """

    if media is None:
        return None
    quality = await session.scalar(
        select(MediaFile.quality).where(
            MediaFile.media_id == media.id, MediaFile.is_active.is_(True)
        )
    )
    definition = await _quality_definition(session, quality)
    return None if definition is None else ExistingFile(quality_rank=definition.weight)


def _core_rule(rule: ContentFilterRule) -> CoreFilterRule:
    return CoreFilterRule(
        id=str(rule.id),
        kind=CoreFilterRuleKind(rule.kind.value),
        pattern=rule.pattern,
        action=FilterAction(rule.action.value),
        enabled=rule.enabled,
    )


async def _fuzzy_duplicate(
    session: AsyncSession, candidate: MetadataCandidate, technical: ProbeResult
) -> Media | None:
    """Step 3's fuzzy half: a near-identical release already active in the library.

    Every threshold - title similarity, studio, release date and duration - lives
    in `detect_duplicate`; this only narrows which active media are worth running
    it against, since a real match always shares the studio the release states.
    """

    if not candidate.studio:
        return None
    incoming = MediaCandidate(
        title=candidate.title,
        studio=candidate.studio,
        release_date=candidate.release_date,
        duration_seconds=technical.duration,
    )
    rows = await session.execute(
        select(Media, MediaFile.duration_seconds, MediaFile.oshash)
        .join(MediaFile, (MediaFile.media_id == Media.id) & MediaFile.is_active.is_(True))
        .where(func.lower(Media.studio) == candidate.studio.strip().casefold())
    )
    for media, duration_seconds, existing_oshash in rows:
        existing = MediaCandidate(
            title=media.title,
            studio=media.studio,
            release_date=media.release_date,
            duration_seconds=duration_seconds,
            oshash=existing_oshash,
        )
        decision = detect_duplicate(incoming, existing)
        if decision.classification is DuplicateClassification.UPGRADE_CANDIDATE:
            return media
    return None


def _adopt_candidate(media: Media, candidate: MetadataCandidate, confidence: float) -> None:
    """A fuzzy match is the newer scrape of the same title, so its metadata wins."""

    media.title = candidate.title
    media.normalized_title = normalize_title(candidate.title)
    media.studio = candidate.studio
    media.release_date = candidate.release_date
    media.confidence = confidence


def _by_normalized_name(names: Sequence[str]) -> dict[str, str]:
    """One entry per name the vocabulary actually holds, first spelling wins.

    A provider answers with "Amateur" and "amateur" in the same list, and both
    resolve to one `Tag` row - inserting each would break the assignment's own
    uniqueness on (media, tag, source).
    """

    unique: dict[str, str] = {}
    for name in (candidate.strip() for candidate in names):
        if name:
            unique.setdefault(name.casefold(), name)
    return unique


async def _record_scene_vocabulary(
    session: AsyncSession,
    media: Media,
    candidate: MetadataCandidate,
    confidence: float,
    provider: str,
) -> None:
    """Write the tags and performers the provider knew onto the record.

    Until this existed the cascade's answer reached `evaluate_filters` and
    stopped there: `/api/media/{id}` answered with empty lists however much a
    provider knew, tag filtering had nothing to match, and the interest
    profile recommendations are built from had no signal at all.

    The source is the provider's own name, so a scrape and an operator's
    correction stay distinguishable - `uq_media_tags_source` keys on it.
    """

    for normalized, name in _by_normalized_name(candidate.tags).items():
        tag = await session.scalar(select(Tag).where(Tag.normalized_name == normalized))
        if tag is None:
            tag = Tag(name=name, normalized_name=normalized)
            session.add(tag)
            await session.flush()
        session.add(
            MediaTag(
                media_id=media.id,
                tag_id=tag.id,
                confidence=confidence,
                source=provider,
            )
        )
    for normalized, name in _by_normalized_name(candidate.performers).items():
        performer = await session.scalar(
            select(Performer).where(Performer.normalized_name == normalized)
        )
        if performer is None:
            performer = Performer(name=name, normalized_name=normalized)
            session.add(performer)
            await session.flush()
        session.add(MediaPerformer(media_id=media.id, performer_id=performer.id))


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
        normalized_title=normalize_title(candidate.title),
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
            codecs=codec_payload(technical),
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
