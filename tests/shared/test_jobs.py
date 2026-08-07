"""Tests for queue-wide job conventions."""

from __future__ import annotations

from typing import Any

import pytest
from arq.worker import Retry

from pornarr_shared.jobs import (
    DEFAULT_QUEUE,
    JOB_MAX_TRIES,
    enqueue_once,
    job,
    job_key,
)


class RecordingRedis:
    """Small stand-in that records the ARQ arguments without needing Redis."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_job(self, function: str, *args: object, **kwargs: object) -> str:
        self.calls.append({"function": function, "args": args, **kwargs})
        return "queued"


def test_job_key_is_stable_across_keyword_order() -> None:
    first = job_key("heartbeat", "library", queue=DEFAULT_QUEUE, media_id="123", force=True)
    second = job_key("heartbeat", "library", queue=DEFAULT_QUEUE, force=True, media_id="123")

    assert first == second
    assert first.startswith("pornarr:default:heartbeat:")


@pytest.mark.asyncio
async def test_enqueue_once_passes_the_deterministic_key_to_arq() -> None:
    redis = RecordingRedis()

    queued = await enqueue_once(redis, "heartbeat", "library", queue=DEFAULT_QUEUE, media_id="123")

    assert queued == "queued"
    assert redis.calls == [
        {
            "function": "heartbeat",
            "args": ("library",),
            "_job_id": job_key("heartbeat", "library", queue=DEFAULT_QUEUE, media_id="123"),
            "_queue_name": DEFAULT_QUEUE,
            "media_id": "123",
        }
    ]


@pytest.mark.asyncio
async def test_job_retries_failures_with_exponential_backoff() -> None:
    async def failing_job(_: dict[str, object]) -> None:
        raise RuntimeError("temporary failure")

    registered = job(failing_job)

    with pytest.raises(Retry) as error:
        await registered.coroutine({"job_try": 3})

    assert error.value.defer_score == 4_000
    assert registered.max_tries == JOB_MAX_TRIES
