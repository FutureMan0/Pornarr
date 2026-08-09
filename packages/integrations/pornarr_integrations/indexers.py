"""Small adapter interface for configured indexers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IndexerCategory:
    id: str
    name: str


class IndexerAdapter(Protocol):
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]: ...
