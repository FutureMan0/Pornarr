"""Periodic unfiltered indexer feeds shared by every release monitor."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pornarr_integrations.health import IndexerFailure, failure_for
from pornarr_integrations.indexers import Release
from pornarr_shared.config import get_settings
from pornarr_shared.jobs import INDEXER_QUEUE, enqueue_once, job
from pornarr_worker.jobs.monitor_match import MONITOR_MATCH_JOB
from pornarr_worker.search import (
    INDEXER_SEARCH_TIMEOUT_SECONDS,
    SearchTarget,
    configured_targets,
    record_search_outcome,
)


def new_releases(releases: Sequence[Release], last_rss_guid: str | None) -> list[Release]:
    """Return only entries ahead of the previously seen feed marker."""

    if last_rss_guid is None:
        return list(releases)
    for index, release in enumerate(releases):
        if release.guid == last_rss_guid:
            return list(releases[:index])
    return list(releases)


def unique_releases(releases: Sequence[Release]) -> list[Release]:
    """Preserve feed order while handling duplicate GUIDs from an indexer."""

    seen: set[str] = set()
    unique = []
    for release in releases:
        if release.guid not in seen:
            unique.append(release)
            seen.add(release.guid)
    return unique


async def fetch_rss(
    target: SearchTarget,
) -> tuple[str, list[Release] | None, Exception | None]:
    if target.skip_status is not None:
        return target.skip_status, None, None
    if target.adapter is None:
        return "unavailable", None, None
    try:
        releases = await asyncio.wait_for(
            target.adapter.rss(
                base_url=target.base_url,
                api_key=target.api_key,
                categories=target.search_categories,
            ),
            INDEXER_SEARCH_TIMEOUT_SECONDS,
        )
        return "completed", releases, None
    except TimeoutError:
        return "timed_out", None, TimeoutError("The indexer RSS feed timed out.")
    except Exception as error:
        failure = failure_for(error)
        status = (
            "authentication_failed"
            if failure is IndexerFailure.AUTHENTICATION
            else "malformed_response"
            if failure is IndexerFailure.MALFORMED_RESPONSE
            else "failed"
        )
        return status, None, error


async def rss_sync(context: dict[str, Any], cycle: int) -> int:
    """Fetch every healthy enabled indexer once and cache only unseen releases."""

    del cycle
    redis = context["redis"]
    targets = await configured_targets(redis)
    outcomes = await asyncio.gather(*(fetch_rss(target) for target in targets))
    discovered = 0
    for target, (status, releases, error) in zip(targets, outcomes, strict=True):
        unseen = unique_releases(new_releases(releases or (), target.last_rss_guid))
        await record_search_outcome(
            redis,
            target.id,
            status,
            unseen,
            error,
            last_rss_guid=releases[0].guid if status == "completed" and releases else None,
        )
        if status == "completed" and unseen:
            await enqueue_once(
                redis,
                MONITOR_MATCH_JOB.name,
                target.id,
                [release.guid for release in unseen],
                queue=INDEXER_QUEUE,
            )
            discovered += len(unseen)
    return discovered


RSS_SYNC_JOB = job(rss_sync)


async def dispatch_rss_sync(context: dict[str, Any]) -> int:
    """Schedule one idempotent indexer-queue job for the current feed interval."""

    interval_seconds = get_settings().rss_sync_interval_minutes * 60
    cycle = int(datetime.now(UTC).timestamp() // interval_seconds)
    queued = await enqueue_once(context["redis"], RSS_SYNC_JOB.name, cycle, queue=INDEXER_QUEUE)
    return int(queued is not None)


RSS_SYNC_DISPATCH_JOB = job(dispatch_rss_sync)
