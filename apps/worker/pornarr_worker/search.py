"""Concurrent, progressively reported multi-indexer searches."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from pornarr_core.dedup import ReleaseCandidate, deduplicate_releases
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.release import ReleaseCache
from pornarr_db.release_cache import ReleaseCacheRepository, normalize_release_title
from pornarr_db.session import session_scope
from pornarr_integrations.health import (
    CircuitBreaker,
    IndexerFailure,
    IndexerHealth,
    failure_for,
)
from pornarr_integrations.indexers import Release, SearchIndexerAdapter
from pornarr_integrations.newznab import NewznabAdapter
from pornarr_integrations.torznab import TorznabAdapter
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import job

INDEXER_SEARCH_TIMEOUT_SECONDS = 10
RELEASE_CACHE_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SearchTarget:
    id: str
    base_url: str
    api_key: str
    adapter: SearchIndexerAdapter | None
    skip_status: str | None = None
    cached_releases: list[Release] | None = None
    priority: int = 0
    healthy: bool = True


@dataclass(slots=True)
class SearchState:
    id: str
    user_id: str
    query: str
    statuses: dict[str, str] = field(default_factory=dict)
    results: list[dict[str, object]] = field(default_factory=list)
    cancelled: bool = False


SearchResultCallback = Callable[[str, str, list[Release] | None, Exception | None], Awaitable[None]]


def search_state_key(search_id: str) -> str:
    return f"pornarr:indexer-search:{search_id}"


async def read_search_state(redis: Any, search_id: str) -> SearchState | None:
    stored = await redis.get(search_state_key(search_id))
    if stored is None:
        return None
    data = json.loads(stored)
    return SearchState(**data)


async def run_search(
    redis: Any,
    *,
    search_id: str,
    user_id: str,
    query: str,
    targets: list[SearchTarget],
    timeout: float = INDEXER_SEARCH_TIMEOUT_SECONDS,
    on_result: SearchResultCallback | None = None,
) -> SearchState:
    """Search all targets concurrently and progressively publish display-ready releases."""
    state = SearchState(
        id=search_id,
        user_id=user_id,
        query=query,
        statuses={target.id: "pending" for target in targets},
    )
    await _store(redis, state)
    tasks = [asyncio.create_task(_search(target, query, timeout)) for target in targets]
    target_by_id = {target.id: target for target in targets}
    discovered: list[tuple[SearchTarget, Release]] = []
    try:
        for task in asyncio.as_completed(tasks):
            target_id, status, releases, error = await task
            state.statuses[target_id] = status
            if releases is not None:
                target = target_by_id[target_id]
                discovered.extend((target, release) for release in releases)
                state.results = _deduplicated_data(discovered)
            if on_result is not None:
                await on_result(target_id, status, releases, error)
            await _store(redis, state)
            await publish_event(
                redis,
                "indexer.search.completed",
                {
                    "search_id": search_id,
                    "indexer_id": target_id,
                    "status": status,
                    "results": state.results,
                },
                user_id=user_id,
            )
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        state.cancelled = True
        for target_id, status in state.statuses.items():
            if status == "pending":
                state.statuses[target_id] = "cancelled"
        await _store(redis, state)
        await publish_event(
            redis, "indexer.search.cancelled", {"search_id": search_id}, user_id=user_id
        )
        raise
    return state


ADAPTERS: dict[str, SearchIndexerAdapter] = {
    "newznab": NewznabAdapter(),
    "torznab": TorznabAdapter(),
}


async def configured_targets(redis: Any) -> list[SearchTarget]:
    """Load enabled indexers without persisting their credentials in search state."""
    breaker = CircuitBreaker(redis)
    async with session_scope() as session:
        indexers = list(
            await session.scalars(
                select(Indexer)
                .where(Indexer.enabled.is_(True))
                .order_by(Indexer.priority, Indexer.name)
            )
        )
        targets = []
        for indexer in indexers:
            state = await breaker.probe(
                str(indexer.id),
                IndexerHealth(indexer.health),
                indexer.last_tested_at,
                _health_reason(indexer.health_reason),
            )
            if state is None:
                targets.append(
                    SearchTarget(
                        id=str(indexer.id),
                        base_url=indexer.base_url,
                        api_key=indexer.api_key,
                        adapter=None,
                        skip_status=IndexerHealth.UNHEALTHY.value,
                        priority=indexer.priority,
                        healthy=False,
                    )
                )
                continue
            indexer.health = state.value
            targets.append(
                SearchTarget(
                    id=str(indexer.id),
                    base_url=indexer.base_url,
                    api_key=indexer.api_key,
                    adapter=ADAPTERS.get(indexer.implementation),
                    priority=indexer.priority,
                )
            )
    return targets


async def search_indexers(
    context: dict[str, Any], search_id: str, user_id: str, query: str
) -> SearchState:
    """ARQ entry point for one user's external-indexer search."""
    redis = context["redis"]
    try:
        return await run_search(
            redis,
            search_id=search_id,
            user_id=user_id,
            query=query,
            targets=(await cached_targets(query)) or await configured_targets(redis),
            on_result=lambda target_id, status, releases, error: record_search_outcome(
                redis, target_id, status, releases, error
            ),
        )
    except asyncio.CancelledError:
        # Search cancellation is intentional (for example, the requester left
        # the page), so it must not become ARQ's normal retry-on-cancellation.
        state = await read_search_state(redis, search_id)
        if state is None:
            raise
        return state


SEARCH_INDEXERS_JOB = job(search_indexers)


async def cleanup_release_cache(_: dict[str, Any]) -> int:
    """Delete cache rows that can no longer answer a search."""
    async with session_scope() as session:
        return await ReleaseCacheRepository(session).expire()


RELEASE_CACHE_CLEANUP_JOB = job(cleanup_release_cache)


async def record_search_outcome(
    redis: Any,
    indexer_id: str,
    status: str,
    releases: list[Release] | None,
    error: Exception | None,
) -> None:
    """Persist one actual query's health and aggregate statistics."""
    if status in {IndexerHealth.UNHEALTHY.value, "unavailable", "cached"}:
        return
    async with session_scope() as session:
        indexer = await session.get(Indexer, UUID(indexer_id))
        stats = await session.get(IndexerStats, UUID(indexer_id))
        if indexer is None or stats is None:
            return
        stats.queries += 1
        if status == "completed":
            if releases is not None:
                await cache_releases(session, indexer.id, releases)
            indexer.health = (await CircuitBreaker(redis).record_success(indexer_id)).value
            indexer.health_reason = None
            indexer.last_error = None
        else:
            failure = _failure(status, error)
            outcome = await CircuitBreaker(redis).record_failure(
                indexer_id, IndexerHealth(indexer.health), failure
            )
            stats.failures += 1
            indexer.health = outcome.health.value
            indexer.health_reason = (
                failure.value if outcome.health is IndexerHealth.UNHEALTHY else None
            )
            indexer.last_error = _safe_error(error, indexer.api_key, failure)
        indexer.last_tested_at = datetime.now(UTC)


async def _search(
    target: SearchTarget, query: str, timeout: float
) -> tuple[str, str, list[Release] | None, Exception | None]:
    if target.cached_releases is not None:
        return target.id, "cached", target.cached_releases, None
    if target.skip_status is not None:
        return target.id, target.skip_status, None, None
    if target.adapter is None:
        return target.id, "unavailable", None, None
    try:
        releases = await asyncio.wait_for(
            target.adapter.search(base_url=target.base_url, api_key=target.api_key, query=query),
            timeout,
        )
        return target.id, "completed", releases, None
    except TimeoutError:
        return target.id, "timed_out", None, TimeoutError("The indexer search timed out.")
    except Exception as error:
        failure = _failure("failed", error)
        status = (
            "authentication_failed"
            if failure is IndexerFailure.AUTHENTICATION
            else "malformed_response"
            if failure is IndexerFailure.MALFORMED_RESPONSE
            else "failed"
        )
        return target.id, status, None, error


async def _store(redis: Any, state: SearchState) -> None:
    await redis.set(
        search_state_key(state.id), json.dumps(asdict(state), default=_json_value), ex=3600
    )


def _release_data(release: Release) -> dict[str, object]:
    # Newznab download URLs can contain the configured API key. A grab flow can
    # reconstruct it from the secured indexer configuration; search state cannot.
    data = cast(dict[str, object], asdict(release, dict_factory=dict))
    data.pop("download_url")
    return cast(
        dict[str, object],
        json.loads(json.dumps(data, default=_json_value)),
    )


def _deduplicated_data(
    discovered: list[tuple[SearchTarget, Release]],
) -> list[dict[str, object]]:
    releases = {f"{target.id}:{release.guid}": (target, release) for target, release in discovered}
    candidates = (
        ReleaseCandidate(
            key=key,
            title=release.title,
            size=release.size,
            published_at=release.published_at,
            info_hash=release.info_hash,
            priority=target.priority,
            healthy=target.healthy,
            completeness=_release_completeness(release),
        )
        for key, (target, release) in releases.items()
    )
    return [
        _display_data(group.primary.key, releases, group.alternates)
        for group in deduplicate_releases(candidates)
    ]


def _display_data(
    primary_key: str,
    releases: dict[str, tuple[SearchTarget, Release]],
    alternates: tuple[ReleaseCandidate, ...],
) -> dict[str, object]:
    target, release = releases[primary_key]
    data = _source_data(target, release)
    data["alternates"] = [
        _source_data(releases[item.key][0], releases[item.key][1]) for item in alternates
    ]
    return data


def _source_data(target: SearchTarget, release: Release) -> dict[str, object]:
    return {"indexer_id": target.id, **_release_data(release)}


def _release_completeness(release: Release) -> int:
    values = (
        release.details_url,
        release.download_url,
        release.published_at,
        release.size,
        release.categories,
        release.seeders,
        release.peers,
        release.info_hash,
        release.magnet_url,
        release.groups,
        release.poster,
        release.parts,
        release.password_protected,
    )
    return sum(value is not None and value != () for value in values)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError


def _failure(status: str, error: Exception | None) -> IndexerFailure:
    if status == "timed_out":
        return IndexerFailure.TIMEOUT
    return failure_for(error)


def _health_reason(value: str | None) -> IndexerFailure | None:
    try:
        return IndexerFailure(value) if value is not None else None
    except ValueError:
        return None


def _safe_error(error: Exception | None, api_key: str, failure: IndexerFailure) -> str:
    return (str(error).replace(api_key, "[redacted]") if error is not None else "") or failure.value


async def cache_releases(session: Any, indexer_id: UUID, releases: list[Release]) -> None:
    repository = ReleaseCacheRepository(session)
    expires_at = datetime.now(UTC) + RELEASE_CACHE_TTL
    for release in releases:
        await repository.upsert(
            ReleaseCache(
                indexer_id=indexer_id,
                guid=release.guid,
                title=release.title,
                normalized_title=normalize_release_title(release.title),
                details_url=release.details_url,
                download_url=release.download_url,
                published_at=release.published_at,
                size=release.size,
                categories=list(release.categories),
                seeders=release.seeders,
                peers=release.peers,
                info_hash=release.info_hash,
                magnet_url=release.magnet_url,
                groups=list(release.groups),
                poster=release.poster,
                parts=release.parts,
                password_protected=release.password_protected,
                raw_payload=_release_data(release),
                expires_at=expires_at,
            )
        )


async def cached_targets(query: str) -> list[SearchTarget]:
    async with session_scope() as session:
        cached = await ReleaseCacheRepository(session).search(query)
        indexers = {
            indexer.id: indexer
            for indexer in await session.scalars(
                select(Indexer).where(Indexer.id.in_({release.indexer_id for release in cached}))
            )
        }
    grouped: dict[UUID, list[Release]] = {}
    for release in cached:
        grouped.setdefault(release.indexer_id, []).append(
            Release(
                guid=release.guid,
                title=release.title,
                details_url=release.details_url,
                download_url=release.download_url,
                published_at=release.published_at,
                size=release.size,
                categories=tuple(release.categories),
                seeders=release.seeders,
                peers=release.peers,
                info_hash=release.info_hash,
                magnet_url=release.magnet_url,
                groups=tuple(release.groups),
                poster=release.poster,
                parts=release.parts,
                password_protected=release.password_protected,
            )
        )
    return [
        SearchTarget(
            str(indexer_id),
            "",
            "",
            None,
            cached_releases=releases,
            priority=indexers[indexer_id].priority,
            healthy=indexers[indexer_id].health != IndexerHealth.UNHEALTHY.value,
        )
        for indexer_id, releases in grouped.items()
    ]
