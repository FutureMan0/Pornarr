"""Small adapter interface for configured indexers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IndexerCategory:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Release:
    """A transport-neutral search result from an external indexer."""

    guid: str
    title: str
    details_url: str | None
    download_url: str | None
    published_at: datetime | None
    size: int | None
    categories: tuple[str, ...]
    seeders: int | None = None
    peers: int | None = None
    info_hash: str | None = None
    magnet_url: str | None = None


class IndexerAdapter(Protocol):
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]: ...
