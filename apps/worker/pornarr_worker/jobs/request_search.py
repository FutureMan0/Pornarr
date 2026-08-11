"""Scheduled discovery and safe automatic grabbing for free-text requests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_core.quality import QualityProfile as CoreQualityProfile
from pornarr_core.quality import QualityVerdict, ReleaseCandidate, decide_quality
from pornarr_db.download_clients import route_download_client
from pornarr_db.downloads import is_release_blocked
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.media import Media
from pornarr_db.models.quality import QualityProfile, QualityProfileItem
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.request import Request, RequestStatus
from pornarr_db.release_cache import ReleaseCacheRepository, normalize_release_title
from pornarr_db.requests import transition_request
from pornarr_db.session import session_scope
from pornarr_integrations.qbittorrent import QbittorrentAdapter
from pornarr_integrations.sabnzbd import SabnzbdAdapter
from pornarr_integrations.submission import release_protocol, submit_release
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import INDEXER_QUEUE, enqueue_once, job
from pornarr_worker.search import configured_targets, record_search_outcome, run_search

REQUEST_SEARCH_INITIAL_DELAY = timedelta(hours=1)
REQUEST_SEARCH_MAX_DELAY = timedelta(days=7)
SUBMISSION_ADAPTERS = {"qbittorrent": QbittorrentAdapter(), "sabnzbd": SabnzbdAdapter()}


def search_retry_delay(attempts: int) -> timedelta:
    """Return one hour, two hours, four hours, then cap re-searches at a week."""

    return min(REQUEST_SEARCH_INITIAL_DELAY * 2 ** max(attempts - 1, 0), REQUEST_SEARCH_MAX_DELAY)


async def dispatch_due_request_searches(context: dict[str, Any]) -> int:
    """Queue each due request once, and make expired monitoring explicit."""

    now = datetime.now(UTC)
    async with session_scope() as session:
        expired = list(
            await session.scalars(
                select(Request)
                .where(
                    Request.status.in_((RequestStatus.SEARCHING, RequestStatus.MONITORING)),
                    Request.search_expires_at.is_not(None),
                    Request.search_expires_at <= now,
                )
                .with_for_update()
            )
        )
        for request in expired:
            await transition_request(session, request, RequestStatus.NOT_FOUND)
            request.next_search_at = None
        due = list(
            await session.execute(
                select(Request.id, Request.search_attempts).where(
                    Request.status.in_((RequestStatus.SEARCHING, RequestStatus.MONITORING)),
                    Request.next_search_at.is_not(None),
                    Request.next_search_at <= now,
                    or_(Request.search_expires_at.is_(None), Request.search_expires_at > now),
                )
            )
        )
    redis = context["redis"]
    for request_id, attempts in due:
        await enqueue_once(
            redis,
            REQUEST_SEARCH_JOB.name,
            str(request_id),
            attempts,
            queue=INDEXER_QUEUE,
        )
    for request in expired:
        await publish_event(
            redis,
            "request.not_found",
            {"request_id": str(request.id), "reason": "search_expired"},
            user_id=str(request.user_id),
        )
    return len(due)


REQUEST_SEARCH_DISPATCH_JOB = job(dispatch_due_request_searches)


async def request_search(context: dict[str, Any], request_id: str, attempts: int) -> str:
    """Search fresh indexer results and grab the best allowed quality candidate."""

    request_uuid = UUID(request_id)
    abandoned_user_id: str | None = None
    async with session_scope() as session:
        request = await session.scalar(
            select(Request).where(Request.id == request_uuid).with_for_update()
        )
        if request is None or request.search_attempts != attempts:
            return "stale"
        if _expired(request, datetime.now(UTC)):
            await transition_request(session, request, RequestStatus.NOT_FOUND)
            request.next_search_at = None
            abandoned_user_id = str(request.user_id)
        elif request.status is RequestStatus.MONITORING:
            await transition_request(session, request, RequestStatus.SEARCHING)
        if abandoned_user_id is None and request.status is not RequestStatus.SEARCHING:
            return "stale"
        if abandoned_user_id is None:
            query = request.query
            user_id = str(request.user_id)

    redis = context["redis"]
    if abandoned_user_id is not None:
        await publish_event(
            redis,
            "request.not_found",
            {"request_id": request_id, "reason": "search_expired"},
            user_id=abandoned_user_id,
        )
        return "not_found"

    await run_search(
        redis,
        search_id=f"request:{request_id}:{attempts}",
        user_id=user_id,
        query=query,
        targets=await configured_targets(redis),
        on_result=lambda indexer_id, status, releases, error: record_search_outcome(
            redis, indexer_id, status, releases, error
        ),
    )

    async with session_scope() as session:
        request = await session.scalar(
            select(Request).where(Request.id == request_uuid).with_for_update()
        )
        if request is None or request.status is not RequestStatus.SEARCHING:
            return "stale"
        release = await _best_release(session, request.query)
        if release is None:
            await _monitor(session, request)
            return "monitoring"
        existing = await session.scalar(
            select(DownloadJob).where(DownloadJob.release_guid == release.guid).limit(1)
        )
        if existing is not None:
            await _attach_release(session, request, release.guid, existing.id)
            return "queued"
        protocol = release_protocol(
            magnet_url=release.magnet_url, download_url=release.download_url
        )
        client = await route_download_client(session, protocol)
        adapter = SUBMISSION_ADAPTERS.get(client.implementation)
        if adapter is None:
            raise RuntimeError(
                f"No submission adapter is registered for {client.implementation!r}."
            )
        client_job_id = await submit_release(
            adapter,
            protocol=protocol,
            host=client.host,
            port=client.port,
            url_base=client.url_base,
            credentials=client.credentials,
            category=client.category,
            priority=client.priority,
            magnet_url=release.magnet_url,
            info_hash=release.info_hash,
            download_url=release.download_url,
        )
        job_row = DownloadJob(
            download_client_id=client.id,
            client_name=client.name,
            protocol=protocol,
            release_guid=release.guid,
            client_job_id=client_job_id,
            status="queued",
            priority=request.priority,
            size_bytes=release.size,
        )
        session.add(job_row)
        await session.flush()
        await _attach_release(session, request, release.guid, job_row.id)
        return "queued"


REQUEST_SEARCH_JOB = job(request_search)


async def _best_release(session: AsyncSession, query: str) -> ReleaseCache | None:
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
    quality_profile = CoreQualityProfile(
        allowed_qualities=frozenset(item.quality_definition.name for item in profile.items),
        cutoff_quality_rank=profile.cutoff_quality.weight,
        minimum_custom_format_score=profile.minimum_custom_format_score,
    )
    best: tuple[int, ReleaseCache] | None = None
    releases = await ReleaseCacheRepository(session).search(query)
    for release in releases:
        if await is_release_blocked(session, release.guid) or await _in_library(session, release):
            continue
        item = _quality_item(release, profile.items)
        if item is None:
            continue
        decision = decide_quality(
            ReleaseCandidate(
                quality=item.quality_definition.name,
                quality_rank=item.quality_definition.weight,
            ),
            quality_profile,
            existing=None,
        )
        if decision.verdict is not QualityVerdict.GRAB:
            continue
        if best is None or decision.score > best[0]:
            best = decision.score, release
    return best[1] if best is not None else None


def _quality_item(
    release: ReleaseCache, items: list[QualityProfileItem]
) -> QualityProfileItem | None:
    normalized_title = normalize_release_title(release.title)
    return next(
        (
            item
            for item in sorted(items, key=lambda item: item.quality_definition.weight, reverse=True)
            if normalize_release_title(item.quality_definition.name) in normalized_title
        ),
        None,
    )


async def _in_library(session: AsyncSession, release: ReleaseCache) -> bool:
    return (
        await session.scalar(
            select(Media.id).where(Media.normalized_title == release.normalized_title).limit(1)
        )
        is not None
    )


async def _monitor(session: AsyncSession, request: Request) -> None:
    await transition_request(session, request, RequestStatus.MONITORING)
    request.search_attempts += 1
    request.next_search_at = datetime.now(UTC) + search_retry_delay(request.search_attempts)


async def _attach_release(
    session: AsyncSession, request: Request, release_guid: str, job_id: UUID
) -> None:
    request.selected_release_guid = release_guid
    request.download_job_id = job_id
    request.next_search_at = None
    if request.status is RequestStatus.SEARCHING:
        await transition_request(session, request, RequestStatus.RESULTS_FOUND)
    if request.status is RequestStatus.RESULTS_FOUND:
        await transition_request(session, request, RequestStatus.QUEUED)


def _expired(request: Request, now: datetime) -> bool:
    if request.search_expires_at is None:
        return False
    current = now if request.search_expires_at.tzinfo is not None else now.replace(tzinfo=None)
    return request.search_expires_at <= current
