"""Circuit-breaker behaviour shared by native indexer adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pornarr_integrations.health import (
    BREAKER_OPEN_SECONDS,
    FAILURE_WINDOW_SECONDS,
    CircuitBreaker,
    IndexerFailure,
    IndexerHealth,
)


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
        return 1

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool | None:
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.expirations[key] = ex
        return True


async def test_three_failures_open_a_breaker_then_a_half_open_probe_recovers() -> None:
    redis = Redis()
    breaker = CircuitBreaker(redis)
    health = IndexerHealth.HEALTHY

    for _ in range(2):
        outcome = await breaker.record_failure("indexer-1", health, IndexerFailure.TRANSIENT)
        assert outcome.health is IndexerHealth.HEALTHY
        health = outcome.health

    outcome = await breaker.record_failure("indexer-1", health, IndexerFailure.TIMEOUT)

    assert outcome.health is IndexerHealth.UNHEALTHY
    assert redis.expirations[breaker.failure_key("indexer-1")] == FAILURE_WINDOW_SECONDS
    opened_at = datetime.now(UTC)
    assert await breaker.probe("indexer-1", outcome.health, opened_at, None) is None

    assert (
        await breaker.probe(
            "indexer-1",
            outcome.health,
            opened_at - timedelta(seconds=BREAKER_OPEN_SECONDS),
            None,
        )
        is IndexerHealth.HALF_OPEN
    )
    assert await breaker.probe("indexer-1", IndexerHealth.HALF_OPEN, opened_at, None) is None

    assert await breaker.record_success("indexer-1") is IndexerHealth.HEALTHY
    assert (
        await breaker.probe("indexer-1", IndexerHealth.HEALTHY, opened_at, None)
        is IndexerHealth.HEALTHY
    )


async def test_authentication_failure_opens_without_a_transient_retry() -> None:
    redis = Redis()
    breaker = CircuitBreaker(redis)

    outcome = await breaker.record_failure(
        "indexer-1", IndexerHealth.HEALTHY, IndexerFailure.AUTHENTICATION
    )

    assert outcome.health is IndexerHealth.UNHEALTHY
    assert outcome.failures == 1
    assert (
        await breaker.probe(
            "indexer-1",
            outcome.health,
            datetime.now(UTC) - timedelta(seconds=BREAKER_OPEN_SECONDS),
            IndexerFailure.AUTHENTICATION,
        )
        is None
    )
