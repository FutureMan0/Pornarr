"""Authenticated local-library search."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, get_current_user
from pornarr_core.eta import Confidence, Protocol, search_estimate, unknown
from pornarr_core.filters import (
    ContentCandidate,
    evaluate_filters,
    resolve_rules,
)
from pornarr_core.filters import FilterAction as CoreFilterAction
from pornarr_core.filters import FilterRule as CoreFilterRule
from pornarr_core.filters import FilterRuleKind as CoreFilterRuleKind
from pornarr_core.matching import parse_release
from pornarr_db.audit import write_audit
from pornarr_db.events import record_user_event
from pornarr_db.media_search import (
    MediaSearch,
    MediaSearchResult,
    MediaSort,
    encode_cursor,
    search_media,
    search_metadata,
)
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterProfileScope,
)
from pornarr_db.models.indexer import Indexer
from pornarr_db.models.playback import UserEventType
from pornarr_db.models.release import ReleaseCache
from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.models.user import User
from pornarr_db.release_cache import normalize_release_title
from pornarr_db.statistics import rolling_average
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import (
    INDEXER_QUEUE,
    INDEXER_SEARCH_JOB_NAME,
    indexer_search_state_key,
)
from pornarr_shared.metrics import measure

router = APIRouter(prefix="/search", tags=["search"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]


class LocalSearchItem(BaseModel):
    id: UUID
    title: str
    studio: str | None
    release_date: date | None
    quality: str | None
    resolution: str | None
    size: int
    duration_seconds: float | None
    performers: list[str]
    tags: list[str]
    relevance: float


class LocalSearchResponse(BaseModel):
    items: list[LocalSearchItem]
    next_cursor: str | None


class IndexerSearchWrite(BaseModel):
    q: Annotated[str, Field(min_length=1, max_length=512)]

    @field_validator("q")
    @classmethod
    def strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class IndexerSearchStartResponse(BaseModel):
    id: UUID


class ExternalSearchSort(StrEnum):
    RELEVANCE = "relevance"
    AGE = "age"
    SIZE = "size"
    QUALITY = "quality"
    SEEDERS = "seeders"
    ESTIMATED_TIME = "estimated_time"


class SearchEstimateResponse(BaseModel):
    low_seconds: int | None
    high_seconds: int | None
    confidence: Confidence


class ExternalSearchItem(BaseModel):
    id: UUID
    guid: str
    title: str
    indexer_id: UUID
    indexer_name: str
    protocol: str
    quality: str | None
    size: int | None
    published_at: datetime | None
    seeders: int | None
    estimate: SearchEstimateResponse


class IndexerSearchState(BaseModel):
    id: str
    user_id: str
    query: str
    statuses: dict[str, str] = Field(default_factory=dict)
    results: dict[str, list[dict[str, object]]] = Field(default_factory=dict)
    cancelled: bool = False


class IndexerSearchResponse(BaseModel):
    id: UUID
    query: str
    statuses: dict[str, str]
    cancelled: bool
    items: list[ExternalSearchItem]


@router.post("/indexers", response_model=IndexerSearchStartResponse, status_code=202)
async def start_indexer_search(
    payload: IndexerSearchWrite, request: Request, user: CurrentUser
) -> IndexerSearchStartResponse:
    """Queue a user's external fan-out without delaying the local search response."""

    search_id = uuid4()
    await request.app.state.redis.set(
        indexer_search_state_key(str(search_id)),
        json.dumps(
            {
                "id": str(search_id),
                "user_id": str(user.id),
                "query": payload.q,
                "statuses": {},
                "results": {},
                "cancelled": False,
            }
        ),
        ex=3600,
    )
    await request.app.state.redis.enqueue_job(
        INDEXER_SEARCH_JOB_NAME,
        str(search_id),
        str(user.id),
        payload.q,
        _queue_name=INDEXER_QUEUE,
    )
    await publish_event(
        request.app.state.redis,
        "search.started",
        {"search_id": str(search_id), "query": payload.q},
        user_id=str(user.id),
    )
    return IndexerSearchStartResponse(id=search_id)


@router.get("/indexers/{search_id}", response_model=IndexerSearchResponse)
async def indexer_search_status(
    search_id: UUID,
    request: Request,
    user: CurrentUser,
    session: Session,
    quality: str | None = None,
    minimum_size_bytes: Annotated[int | None, Query(ge=0)] = None,
    maximum_size_bytes: Annotated[int | None, Query(ge=0)] = None,
    maximum_age_days: Annotated[int | None, Query(ge=0, le=36_500)] = None,
    indexer_id: UUID | None = None,
    protocol: Annotated[str | None, Query(max_length=32)] = None,
    minimum_seeders: Annotated[int | None, Query(ge=0)] = None,
    sort: ExternalSearchSort = ExternalSearchSort.RELEVANCE,
) -> IndexerSearchResponse:
    """Read one user's progressive indexer search with stable, shared filters."""

    _validate_size_range(minimum_size_bytes, maximum_size_bytes)
    stored = await request.app.state.redis.get(indexer_search_state_key(str(search_id)))
    if stored is None:
        raise HTTPException(status_code=404)
    state = IndexerSearchState.model_validate_json(stored)
    if state.user_id != str(user.id):
        raise HTTPException(status_code=404)
    items = await _external_items(
        session,
        state,
        quality=quality,
        minimum_size_bytes=minimum_size_bytes,
        maximum_size_bytes=maximum_size_bytes,
        maximum_age_days=maximum_age_days,
        indexer_id=indexer_id,
        protocol=protocol,
        minimum_seeders=minimum_seeders,
        sort=sort,
    )
    return IndexerSearchResponse(
        id=search_id,
        query=state.query,
        statuses=state.statuses,
        cancelled=state.cancelled,
        items=items,
    )


@router.get("/local", response_model=LocalSearchResponse)
async def local_search(
    user: CurrentUser,
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=512)],
    quality: str | None = None,
    year: Annotated[int | None, Query(ge=1900, le=9999)] = None,
    studio: str | None = None,
    performer: str | None = None,
    tag: str | None = None,
    minimum_duration_seconds: Annotated[float | None, Query(ge=0)] = None,
    maximum_duration_seconds: Annotated[float | None, Query(ge=0)] = None,
    minimum_size_bytes: Annotated[int | None, Query(ge=0)] = None,
    maximum_size_bytes: Annotated[int | None, Query(ge=0)] = None,
    maximum_age_days: Annotated[int | None, Query(ge=0, le=36_500)] = None,
    sort: MediaSort = MediaSort.RELEVANCE,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> LocalSearchResponse:
    with measure("search"):
        try:
            _validate_size_range(minimum_size_bytes, maximum_size_bytes)
            search = MediaSearch(
                query=q,
                quality=quality,
                year=year,
                studio=studio,
                performer=performer,
                tag=tag,
                minimum_duration_seconds=minimum_duration_seconds,
                maximum_duration_seconds=maximum_duration_seconds,
                minimum_size_bytes=minimum_size_bytes,
                maximum_size_bytes=maximum_size_bytes,
                maximum_age_days=maximum_age_days,
                sort=sort,
                cursor=cursor,
                limit=limit + 1,
            )
            results = await search_media(session, search)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        page = results[:limit]
        metadata = await search_metadata(session, [result.media.id for result in page])
        rules = await _rules_for_user(session, user.id)
        items = []
        for result in page:
            decision = _filter_decision(result, metadata[result.media.id], rules)
            if decision.action is CoreFilterAction.ALLOW:
                items.append(_search_item(result, metadata[result.media.id]))
            elif decision.action is CoreFilterAction.REJECT and decision.rule is not None:
                write_audit(
                    session,
                    actor_id=user.id,
                    action="filter.rejected",
                    target=str(result.media.id),
                    context={"rule_id": decision.rule.id},
                )
        next_cursor = _next_cursor(results, limit, sort, q)
        await record_user_event(session, user.id, UserEventType.SEARCH, value=float(len(items)))
        return LocalSearchResponse(items=items, next_cursor=next_cursor)


def _validate_size_range(minimum_size_bytes: int | None, maximum_size_bytes: int | None) -> None:
    if (
        minimum_size_bytes is not None
        and maximum_size_bytes is not None
        and minimum_size_bytes > maximum_size_bytes
    ):
        raise HTTPException(status_code=422, detail="minimum_size_bytes exceeds maximum_size_bytes")


async def _external_items(
    session: AsyncSession,
    state: IndexerSearchState,
    *,
    quality: str | None,
    minimum_size_bytes: int | None,
    maximum_size_bytes: int | None,
    maximum_age_days: int | None,
    indexer_id: UUID | None,
    protocol: str | None,
    minimum_seeders: int | None,
    sort: ExternalSearchSort,
) -> list[ExternalSearchItem]:
    keys = _release_keys(state.results)
    if not keys:
        return []
    indexer_ids = {key[0] for key in keys}
    indexers = {
        indexer.id: indexer
        for indexer in await session.scalars(select(Indexer).where(Indexer.id.in_(indexer_ids)))
    }
    releases = [
        release
        for release in await session.scalars(
            select(ReleaseCache).where(ReleaseCache.indexer_id.in_(indexer_ids))
        )
        if (release.indexer_id, release.guid) in keys
    ]
    speeds = await _download_speeds(session, {indexer.protocol for indexer in indexers.values()})
    items = [
        _external_item(release, indexers[release.indexer_id], speeds)
        for release in releases
        if release.indexer_id in indexers
    ]
    filtered = [
        item
        for item in items
        if _matches_external(
            item,
            quality=quality,
            minimum_size_bytes=minimum_size_bytes,
            maximum_size_bytes=maximum_size_bytes,
            maximum_age_days=maximum_age_days,
            indexer_id=indexer_id,
            protocol=protocol,
            minimum_seeders=minimum_seeders,
        )
    ]
    return sorted(filtered, key=lambda item: _external_sort_key(item, state.query, sort))


def _release_keys(results: dict[str, list[dict[str, object]]]) -> set[tuple[UUID, str]]:
    keys: set[tuple[UUID, str]] = set()
    for indexer_id, releases in results.items():
        try:
            parsed_indexer_id = UUID(indexer_id)
        except ValueError:
            continue
        for release in releases:
            guid = release.get("guid")
            if isinstance(guid, str):
                keys.add((parsed_indexer_id, guid))
    return keys


async def _download_speeds(session: AsyncSession, protocols: set[str]) -> dict[str, int]:
    speeds: dict[str, int] = {}
    for protocol in protocols:
        summary = await rolling_average(
            session, metric=PerformanceMetric.DOWNLOAD_SPEED, scope=protocol
        )
        if summary.value is not None:
            speeds[protocol] = int(summary.value)
    return speeds


def _external_item(
    release: ReleaseCache, indexer: Indexer, speeds: dict[str, int]
) -> ExternalSearchItem:
    quality = parse_release(release.title).resolution
    try:
        protocol = Protocol(indexer.protocol)
    except ValueError:
        estimate = unknown()
    else:
        estimate = (
            search_estimate(
                protocol,
                release.size,
                speeds.get(indexer.protocol, 0),
                seeders=release.seeders,
            )
            if release.size is not None
            else unknown()
        )
    return ExternalSearchItem(
        id=release.id,
        guid=release.guid,
        title=release.title,
        indexer_id=indexer.id,
        indexer_name=indexer.name,
        protocol=indexer.protocol,
        quality=quality,
        size=release.size,
        published_at=_as_utc(release.published_at),
        seeders=release.seeders,
        estimate=SearchEstimateResponse(
            low_seconds=estimate.low_seconds,
            high_seconds=estimate.high_seconds,
            confidence=estimate.confidence,
        ),
    )


def _matches_external(
    item: ExternalSearchItem,
    *,
    quality: str | None,
    minimum_size_bytes: int | None,
    maximum_size_bytes: int | None,
    maximum_age_days: int | None,
    indexer_id: UUID | None,
    protocol: str | None,
    minimum_seeders: int | None,
) -> bool:
    if quality is not None and item.quality != quality:
        return False
    if minimum_size_bytes is not None and (item.size is None or item.size < minimum_size_bytes):
        return False
    if maximum_size_bytes is not None and (item.size is None or item.size > maximum_size_bytes):
        return False
    if maximum_age_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=maximum_age_days)
        if item.published_at is None or item.published_at < cutoff:
            return False
    if indexer_id is not None and item.indexer_id != indexer_id:
        return False
    if protocol is not None and item.protocol != protocol:
        return False
    return minimum_seeders is None or (item.seeders or 0) >= minimum_seeders


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _external_sort_key(
    item: ExternalSearchItem, query: str, sort: ExternalSearchSort
) -> tuple[object, ...]:
    tie_breaker = (item.title.casefold(), str(item.id))
    if sort is ExternalSearchSort.AGE:
        timestamp = item.published_at.timestamp() if item.published_at is not None else 0
        return (item.published_at is None, -timestamp, *tie_breaker)
    if sort is ExternalSearchSort.SIZE:
        return (item.size is None, -(item.size or 0), *tie_breaker)
    if sort is ExternalSearchSort.QUALITY:
        return (item.quality is None, -_quality_rank(item.quality), *tie_breaker)
    if sort is ExternalSearchSort.SEEDERS:
        return (item.seeders is None, -(item.seeders or 0), *tie_breaker)
    if sort is ExternalSearchSort.ESTIMATED_TIME:
        return (item.estimate.high_seconds is None, item.estimate.high_seconds or 0, *tie_breaker)
    return (
        -SequenceMatcher(
            None, normalize_release_title(query), normalize_release_title(item.title)
        ).ratio(),
        *tie_breaker,
    )


def _quality_rank(quality: str | None) -> int:
    if quality is None or not quality.endswith("p"):
        return -1
    try:
        return int(quality[:-1])
    except ValueError:
        return -1


async def _rules_for_user(session: AsyncSession, user_id: UUID) -> tuple[CoreFilterRule, ...]:
    profiles = await session.scalars(
        select(ContentFilterProfile)
        .options(selectinload(ContentFilterProfile.rules))
        .where(
            or_(
                ContentFilterProfile.scope == FilterProfileScope.GLOBAL,
                ContentFilterProfile.user_id == user_id,
            )
        )
    )
    global_rules: list[CoreFilterRule] = []
    user_rules: list[CoreFilterRule] = []
    for profile in profiles:
        target = global_rules if profile.scope is FilterProfileScope.GLOBAL else user_rules
        target.extend(_core_rule(rule) for rule in profile.rules)
    return resolve_rules(global_rules, user_rules)


def _core_rule(rule: ContentFilterRule) -> CoreFilterRule:
    return CoreFilterRule(
        id=str(rule.id),
        kind=CoreFilterRuleKind(rule.kind.value),
        pattern=rule.pattern,
        action=CoreFilterAction(rule.action.value),
        enabled=rule.enabled,
    )


def _filter_decision(result: MediaSearchResult, metadata, rules: tuple[CoreFilterRule, ...]):
    candidate = ContentCandidate(
        title=result.media.title,
        description=result.media.description or "",
        tags=metadata.tags,
        performers=metadata.performers,
        metadata_confidence=result.media.confidence or 0.0,
        has_unknown_performer_age=metadata.has_unknown_performer_age,
        file_type=result.media_file.container,
    )
    return evaluate_filters(candidate, rules)


def _search_item(result: MediaSearchResult, metadata) -> LocalSearchItem:
    return LocalSearchItem(
        id=result.media.id,
        title=result.media.title,
        studio=result.media.studio,
        release_date=result.media.release_date,
        quality=result.media_file.quality,
        resolution=result.media_file.resolution,
        size=result.media_file.size,
        duration_seconds=result.media_file.duration_seconds,
        performers=sorted(metadata.performers),
        tags=sorted(metadata.tags),
        relevance=result.relevance,
    )


def _next_cursor(
    results: list[MediaSearchResult], limit: int, sort: MediaSort, query: str
) -> str | None:
    if len(results) <= limit:
        return None
    result = results[limit - 1]
    values: dict[MediaSort, object] = {
        MediaSort.RELEVANCE: 0.0
        if result.media.normalized_title == query.casefold()
        else 1.0 - result.relevance,
        MediaSort.DATE_ADDED: result.media.created_at,
        MediaSort.TITLE: result.media.normalized_title,
        MediaSort.SIZE: result.media_file.size,
        MediaSort.DURATION: result.media_file.duration_seconds
        if result.media_file.duration_seconds is not None
        else -1,
    }
    return encode_cursor((values[sort], result.media.id))
