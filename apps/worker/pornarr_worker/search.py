"""Concurrent, progressively reported multi-indexer searches."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select

from pornarr_db.models.indexer import Indexer
from pornarr_db.session import session_scope
from pornarr_integrations.indexers import Release, SearchIndexerAdapter
from pornarr_integrations.newznab import NewznabAdapter
from pornarr_integrations.torznab import TorznabAdapter
from pornarr_shared.events import publish_event
from pornarr_shared.jobs import job

INDEXER_SEARCH_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class SearchTarget:
    id: str
    base_url: str
    api_key: str
    adapter: SearchIndexerAdapter | None


@dataclass(slots=True)
class SearchState:
    id: str
    user_id: str
    query: str
    statuses: dict[str, str] = field(default_factory=dict)
    results: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    cancelled: bool = False


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
) -> SearchState:
    """Search all targets concurrently, preserving each completed result immediately."""
    state = SearchState(
        id=search_id,
        user_id=user_id,
        query=query,
        statuses={target.id: "pending" for target in targets},
    )
    await _store(redis, state)
    tasks = [asyncio.create_task(_search(target, query, timeout)) for target in targets]
    try:
        for task in asyncio.as_completed(tasks):
            target_id, status, releases = await task
            state.statuses[target_id] = status
            if releases is not None:
                state.results[target_id] = [_release_data(release) for release in releases]
            await _store(redis, state)
            await publish_event(
                redis,
                "indexer.search.completed",
                {
                    "search_id": search_id,
                    "indexer_id": target_id,
                    "status": status,
                    "results": state.results.get(target_id, []),
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


async def configured_targets() -> list[SearchTarget]:
    """Load enabled indexers without persisting their credentials in search state."""
    async with session_scope() as session:
        indexers = list(
            await session.scalars(
                select(Indexer)
                .where(Indexer.enabled.is_(True))
                .order_by(Indexer.priority, Indexer.name)
            )
        )
    return [
        SearchTarget(
            id=str(indexer.id),
            base_url=indexer.base_url,
            api_key=indexer.api_key,
            adapter=ADAPTERS.get(indexer.implementation),
        )
        for indexer in indexers
    ]


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
            targets=await configured_targets(),
        )
    except asyncio.CancelledError:
        # Search cancellation is intentional (for example, the requester left
        # the page), so it must not become ARQ's normal retry-on-cancellation.
        state = await read_search_state(redis, search_id)
        if state is None:
            raise
        return state


SEARCH_INDEXERS_JOB = job(search_indexers)


async def _search(
    target: SearchTarget, query: str, timeout: float
) -> tuple[str, str, list[Release] | None]:
    if target.adapter is None:
        return target.id, "unavailable", None
    try:
        releases = await asyncio.wait_for(
            target.adapter.search(base_url=target.base_url, api_key=target.api_key, query=query),
            timeout,
        )
        return target.id, "completed", releases
    except TimeoutError:
        return target.id, "timed_out", None
    except Exception:
        return target.id, "failed", None


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


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError
