"""Small adapter interface for configured download clients."""

from __future__ import annotations

from typing import Protocol


class DownloadClientAdapter(Protocol):
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None: ...
