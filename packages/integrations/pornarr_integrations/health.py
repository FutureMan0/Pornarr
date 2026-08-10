"""Stateful circuit breaking for external indexers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

FAILURE_THRESHOLD = 3
FAILURE_WINDOW_SECONDS = 300
BREAKER_OPEN_SECONDS = 300
HALF_OPEN_PROBE_SECONDS = 30


class IndexerHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    HALF_OPEN = "half-open"


class IndexerFailure(StrEnum):
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    health: IndexerHealth
    failures: int


class CircuitBreaker:
    """Keep failure-window and probe-lock details behind a small async interface."""

    def __init__(self, redis: Any) -> None:
        self.redis = redis

    def failure_key(self, indexer_id: str) -> str:
        return f"pornarr:indexer-health:{indexer_id}:failures"

    def probe_key(self, indexer_id: str) -> str:
        return f"pornarr:indexer-health:{indexer_id}:probe"

    async def probe(
        self,
        indexer_id: str,
        health: IndexerHealth,
        opened_at: datetime | None,
        failure: IndexerFailure | None,
    ) -> IndexerHealth | None:
        """Return the state for an allowed request, or ``None`` while open."""
        if health in (IndexerHealth.UNKNOWN, IndexerHealth.HEALTHY):
            return health
        if failure is IndexerFailure.AUTHENTICATION:
            return None
        if (
            health is IndexerHealth.UNHEALTHY
            and opened_at is not None
            and opened_at > datetime.now(UTC) - timedelta(seconds=BREAKER_OPEN_SECONDS)
        ):
            return None
        claimed = await self.redis.set(
            self.probe_key(indexer_id), "1", ex=HALF_OPEN_PROBE_SECONDS, nx=True
        )
        return IndexerHealth.HALF_OPEN if claimed else None

    async def record_success(self, indexer_id: str) -> IndexerHealth:
        await self.redis.delete(self.failure_key(indexer_id), self.probe_key(indexer_id))
        return IndexerHealth.HEALTHY

    async def record_failure(
        self, indexer_id: str, health: IndexerHealth, failure: IndexerFailure
    ) -> FailureOutcome:
        failures = await self.redis.incr(self.failure_key(indexer_id))
        if failures == 1:
            await self.redis.expire(self.failure_key(indexer_id), FAILURE_WINDOW_SECONDS)
        if failure is IndexerFailure.AUTHENTICATION or health is IndexerHealth.HALF_OPEN:
            await self.redis.delete(self.probe_key(indexer_id))
            return FailureOutcome(IndexerHealth.UNHEALTHY, failures)
        if failures >= FAILURE_THRESHOLD:
            await self.redis.delete(self.probe_key(indexer_id))
            return FailureOutcome(IndexerHealth.UNHEALTHY, failures)
        return FailureOutcome(health, failures)

    async def reset(self, indexer_id: str) -> IndexerHealth:
        await self.redis.delete(self.failure_key(indexer_id), self.probe_key(indexer_id))
        return IndexerHealth.UNKNOWN


def failure_for(error: Exception | None) -> IndexerFailure:
    """Classify an adapter error without coupling this module to an adapter class."""
    failure = getattr(error, "failure", None)
    if isinstance(failure, IndexerFailure):
        return failure
    return IndexerFailure.TRANSIENT
