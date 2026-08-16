from datetime import UTC, datetime

from pornarr_core.dedup import IndexedRelease, deduplicate
from pornarr_integrations.indexers import Release


def release(
    guid: str,
    *,
    info_hash: str | None = None,
    title: str = "Sample 1080p",
    size: int = 1_000,
) -> Release:
    return Release(
        guid=guid,
        title=title,
        details_url=None,
        download_url="url",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        size=size,
        categories=(),
        info_hash=info_hash,
    )


def test_keeps_best_indexer_and_exposes_same_hash_alternates() -> None:
    groups = deduplicate(
        [
            IndexedRelease("low", 10, release("a", info_hash="abc")),
            IndexedRelease("best", 1, release("b", info_hash="ABC")),
            IndexedRelease("other", 5, release("c", info_hash="abc")),
        ]
    )
    assert groups[0].primary.indexer_id == "best"
    assert [item.indexer_id for item in groups[0].alternates] == ["other", "low"]


def test_groups_small_size_variance_but_not_distinct_releases() -> None:
    groups = deduplicate(
        [
            IndexedRelease("one", 1, release("a")),
            IndexedRelease("two", 2, release("b", size=1_020)),
            IndexedRelease("three", 3, release("c", title="Other", size=1_020)),
        ]
    )
    assert len(groups) == 2
