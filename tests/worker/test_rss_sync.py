"""Indexer RSS synchronization remains one request per enabled source."""

from __future__ import annotations

import json
from pathlib import Path

from pornarr_integrations.indexers import IndexerCategory, Release
from pornarr_worker.jobs import rss_sync
from pornarr_worker.search import SearchTarget


class Adapter:
    def __init__(self, releases: list[Release], *, fail: bool = False) -> None:
        self.releases = releases
        self.fail = fail
        self.calls = 0

    async def rss(self, *, base_url: str, api_key: str) -> list[Release]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("unavailable")
        return self.releases

    async def search(self, *, base_url: str, api_key: str, query: str) -> list[Release]:
        return self.releases

    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        return []


def _release(guid: str) -> Release:
    return Release(guid, f"Release {guid}", None, None, None, None, ())


def _recorded_feed() -> list[Release]:
    fixture = Path(__file__).with_name("fixtures") / "rss-feed-duplicates.json"
    return [_release(item["guid"]) for item in json.loads(fixture.read_text())]


async def test_sync_deduplicates_a_recorded_rss_feed_before_matching(monkeypatch) -> None:
    target = SearchTarget("recorded", "https://recorded", "key", Adapter(_recorded_feed()))
    recorded: list[list[str]] = []
    queued: list[list[str]] = []

    async def configured_targets(_: object) -> list[SearchTarget]:
        return [target]

    async def record(
        _: object, __: str, ___: str, releases, ____: object, *, last_rss_guid: str | None
    ) -> None:
        assert last_rss_guid == "fresh-release"
        recorded.append([release.guid for release in releases or []])

    async def enqueue(_: object, __: str, ___: str, guids: list[str], *, queue: str) -> object:
        assert queue == "pornarr:indexer"
        queued.append(guids)
        return object()

    monkeypatch.setattr(rss_sync, "configured_targets", configured_targets)
    monkeypatch.setattr(rss_sync, "record_search_outcome", record)
    monkeypatch.setattr(rss_sync, "enqueue_once", enqueue)

    discovered = await rss_sync.rss_sync({"redis": object()}, cycle=1)

    assert discovered == 2
    assert recorded == [["fresh-release", "duplicate-release"]]
    assert queued == [["fresh-release", "duplicate-release"]]


async def test_sync_fetches_each_healthy_indexer_once_and_only_caches_new_guids(
    monkeypatch,
) -> None:
    first = Adapter([_release("new"), _release("seen"), _release("old")])
    second = Adapter([_release("other")])
    unhealthy = Adapter([_release("ignored")])
    targets = [
        SearchTarget("first", "https://first", "key", first, last_rss_guid="seen"),
        SearchTarget("second", "https://second", "key", second),
        SearchTarget("unhealthy", "https://down", "key", unhealthy, "unhealthy"),
    ]
    recorded: list[tuple[str, str, list[Release] | None, str | None]] = []
    queued: list[tuple[str, str, list[str]]] = []

    async def configured_targets(_: object) -> list[SearchTarget]:
        return targets

    async def record(
        _: object, indexer_id: str, status: str, releases, error, *, last_rss_guid: str | None
    ) -> None:
        assert error is None
        recorded.append((indexer_id, status, releases, last_rss_guid))

    async def enqueue(_: object, function: str, indexer_id: str, guids: list[str], *, queue: str):
        queued.append((function, indexer_id, guids))
        return object()

    monkeypatch.setattr(rss_sync, "configured_targets", configured_targets)
    monkeypatch.setattr(rss_sync, "record_search_outcome", record)
    monkeypatch.setattr(rss_sync, "enqueue_once", enqueue)

    discovered = await rss_sync.rss_sync({"redis": object()}, cycle=1)

    assert discovered == 2
    assert first.calls == second.calls == 1
    assert unhealthy.calls == 0
    assert [
        (indexer_id, status, [release.guid for release in releases or []], last_rss_guid)
        for indexer_id, status, releases, last_rss_guid in recorded
    ] == [
        ("first", "completed", ["new"], "new"),
        ("second", "completed", ["other"], "other"),
        ("unhealthy", "unhealthy", [], None),
    ]
    assert queued == [
        ("monitor_match", "first", ["new"]),
        ("monitor_match", "second", ["other"]),
    ]


async def test_failed_rss_feed_is_recorded_for_indexer_health(monkeypatch) -> None:
    target = SearchTarget("failed", "https://failed", "key", Adapter([], fail=True))
    recorded: list[tuple[str, str, Exception | None]] = []

    async def configured_targets(_: object) -> list[SearchTarget]:
        return [target]

    async def record(
        _: object, indexer_id: str, status: str, releases, error, *, last_rss_guid: str | None
    ) -> None:
        assert last_rss_guid is None
        recorded.append((indexer_id, status, error))

    monkeypatch.setattr(rss_sync, "configured_targets", configured_targets)
    monkeypatch.setattr(rss_sync, "record_search_outcome", record)

    assert await rss_sync.rss_sync({"redis": object()}, cycle=1) == 0
    assert recorded[0][:2] == ("failed", "failed")
    assert isinstance(recorded[0][2], RuntimeError)


def test_new_releases_stops_at_the_last_seen_guid() -> None:
    assert [
        release.guid
        for release in rss_sync.new_releases(
            [_release("new"), _release("seen"), _release("old")], "seen"
        )
    ] == ["new"]
