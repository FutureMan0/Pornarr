"""Queue and retry conventions shared by every background job."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from arq.worker import Function, Retry, func

from pornarr_shared.logging import reset_job_id, set_job_id

DEFAULT_QUEUE = "pornarr:default"
IMPORT_QUEUE = "pornarr:import"
TRANSCODE_QUEUE = "pornarr:transcode"
INDEXER_QUEUE = "pornarr:indexer"
BACKLOG_SEARCH_JOB_NAME = "backlog_search"
SCAN_JOB_NAME = "scan"
INDEXER_SEARCH_JOB_NAME = "search_indexers"
SCAN_JOB_NAME = "scan"

JOB_TIMEOUT_SECONDS = 300
JOB_MAX_TRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 1
JOB_COMPLETION_WAIT_SECONDS = 30
WORKER_HEALTH_KEY = f"{DEFAULT_QUEUE}:health-check"

JobCoroutine = Callable[..., Awaitable[Any]]


def indexer_search_state_key(search_id: str) -> str:
    """Return the Redis key for one user's transient indexer-search state."""

    return f"pornarr:indexer-search:{search_id}"


def job_key(function: str, *args: object, queue: str, **kwargs: object) -> str:
    """Return the stable ARQ id for one logical unit of work.

    ARQ retains a job id while it is queued, running, or has a result. Reusing
    this id therefore makes a duplicate enqueue a no-op and lets ARQ retries
    keep working on the same job rather than create another copy.
    """

    try:
        payload = json.dumps(
            {"args": args, "kwargs": kwargs},
            sort_keys=True,
            separators=(",", ":"),
        )
    except TypeError as exc:
        raise TypeError("Job arguments must be JSON serializable to be idempotent") from exc
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"{queue}:{function}:{digest}"


async def enqueue_once(
    redis: Any,
    function: str,
    *args: object,
    queue: str = DEFAULT_QUEUE,
    **kwargs: object,
) -> Any:
    """Enqueue a job once, returning ``None`` when that work already exists."""

    return await redis.enqueue_job(
        function,
        *args,
        _job_id=job_key(function, *args, queue=queue, **kwargs),
        _queue_name=queue,
        **kwargs,
    )


def retry_delay_seconds(job_try: int) -> float:
    """Use one, two, four… seconds between attempts."""

    return RETRY_BACKOFF_BASE_SECONDS * 2 ** max(job_try - 1, 0)


def job(
    coroutine: JobCoroutine,
    *,
    timeout: float = JOB_TIMEOUT_SECONDS,
    max_tries: int = JOB_MAX_TRIES,
) -> Function:
    """Register a job with the project timeout and retry contract."""

    @wraps(coroutine)
    async def retrying_coroutine(context: dict[str, Any], *args: object, **kwargs: object) -> Any:
        job_id = context.get("job_id")
        token = set_job_id(job_id) if isinstance(job_id, str) else None
        try:
            return await coroutine(context, *args, **kwargs)
        except asyncio.CancelledError:
            # ``asyncio.wait_for`` cancels a timed-out job. Turning that
            # cancellation into Retry gives timeouts the same backoff as errors.
            raise Retry(defer=retry_delay_seconds(int(context["job_try"]))) from None
        except Retry:
            raise
        except Exception:
            raise Retry(defer=retry_delay_seconds(int(context["job_try"]))) from None
        finally:
            if token is not None:
                reset_job_id(token)

    return func(retrying_coroutine, timeout=timeout, max_tries=max_tries)
