"""Progressive multi-indexer search behaviour."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_integrations.indexers import IndexerCategory, Release
from pornarr_worker import search
from pornarr_worker.search import SearchTarget, read_search_state, run_search


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.events: list[tuple[str, dict[str, object]]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
        return deleted

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, _: str, seconds: int) -> bool:
        return seconds > 0

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        return True

    async def xadd(self, _: str, fields: dict[str, str]) -> str:
        self.events.append((fields["type"], json.loads(fields["data"])))
        return "1-0"

    async def publish(self, *_: object) -> None:
        return None


class Adapter:
    def __init__(self, delay: float, *, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.cancelled = False

    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        return []

    async def search(self, *, base_url: str, api_key: str, query: str) -> list[Release]:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise RuntimeError
        return [Release("one", "Example", None, None, None, None, ())]


async def test_results_are_progressive_and_one_timeout_does_not_block_others() -> None:
    redis = Redis()
    failed = Adapter(0.005, fail=True)
    state = await run_search(
        redis,
        search_id="search-1",
        user_id="user-1",
        query="example",
        targets=[
            SearchTarget("fast", "https://fast", "key", Adapter(0)),
            SearchTarget("broken", "https://broken", "key", failed),
            SearchTarget("slow", "https://slow", "key", Adapter(1)),
        ],
        timeout=0.01,
    )

    assert state.statuses == {"fast": "completed", "broken": "failed", "slow": "timed_out"}
    assert state.results["fast"][0]["title"] == "Example"
    assert "download_url" not in state.results["fast"][0]
    assert redis.events[0][0] == "indexer.search.completed"
    assert redis.events[0][1]["indexer_id"] == "fast"
    assert (await read_search_state(redis, "search-1")) == state


async def test_cancelling_a_search_job_stops_pending_requests(monkeypatch) -> None:
    redis = Redis()
    adapter = Adapter(10)
    targets = [SearchTarget("slow", "https://slow", "key", adapter)]

    async def configured_targets(_: Redis) -> list[SearchTarget]:
        return targets

    monkeypatch.setattr(search, "configured_targets", configured_targets)
    task = asyncio.create_task(
        search.SEARCH_INDEXERS_JOB.coroutine(
            {"redis": redis},
            "search-2",
            "user-1",
            "example",
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    result = await task
    state = await read_search_state(redis, "search-2")
    assert state is not None and state.cancelled is True
    assert state == result
    assert state.statuses == {"slow": "cancelled"}
    assert adapter.cancelled is True
    assert redis.events[-1] == ("indexer.search.cancelled", {"search_id": "search-2"})


async def test_unhealthy_indexers_are_reported_without_a_request() -> None:
    redis = Redis()

    state = await run_search(
        redis,
        search_id="search-3",
        user_id="user-1",
        query="example",
        targets=[SearchTarget("unhealthy", "https://down", "key", None, "unhealthy")],
    )

    assert state.statuses == {"unhealthy": "unhealthy"}
    assert redis.events == [
        (
            "indexer.search.completed",
            {
                "search_id": "search-3",
                "indexer_id": "unhealthy",
                "status": "unhealthy",
                "results": [],
            },
        )
    ]


async def test_worker_outcomes_persist_failure_health_and_redacted_error(monkeypatch) -> None:
    indexer_id = uuid4()
    indexer = Indexer(
        id=indexer_id,
        name="example",
        protocol="torznab",
        implementation="torznab",
        base_url="https://indexer.example",
        api_key="secret-key",
        health="healthy",
    )
    stats = IndexerStats(indexer_id=indexer_id, queries=0, failures=0, grabs=0)

    class Session:
        async def get(self, model: type[object], value: UUID) -> Indexer | IndexerStats | None:
            if value != indexer_id:
                return None
            return indexer if model is Indexer else stats if model is IndexerStats else None

    @asynccontextmanager
    async def session_scope():
        yield Session()

    monkeypatch.setattr(search, "session_scope", session_scope)
    redis = Redis()
    for _ in range(3):
        await search.record_search_outcome(
            redis,
            str(indexer_id),
            "timed_out",
            TimeoutError("secret-key timed out"),
        )

    assert stats.queries == 3
    assert stats.failures == 3
    assert indexer.health == "unhealthy"
    assert indexer.health_reason == "timeout"
    assert indexer.last_error == "[redacted] timed out"

    await search.record_search_outcome(redis, str(indexer_id), "completed", None)

    assert stats.queries == 4
    assert stats.failures == 3
    assert indexer.health == "healthy"
    assert indexer.health_reason is None
    assert indexer.last_error is None
