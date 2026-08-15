"""Protocol-neutral metadata provider contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    """Normalized scene metadata returned by a configured provider."""

    title: str
    site: str | None = None
    release_date: date | None = None
    performers: tuple[str, ...] = ()
    studio: str | None = None
    tags: tuple[str, ...] = ()
    provider_id: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


class MetadataRateLimitError(Exception):
    """A provider asked callers to pause before retrying one request."""

    def __init__(self, retry_after_seconds: float) -> None:
        if retry_after_seconds <= 0:
            raise ValueError("retry_after_seconds must be positive")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"metadata provider is rate limited for {retry_after_seconds} seconds")


class MetadataProviderAdapter(Protocol):
    """The common lookup surface used by the confidence cascade."""

    name: str

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None: ...

    async def find_by_site_date_title(
        self, *, site: str, release_date: date, title: str
    ) -> MetadataCandidate | None: ...

    async def search(
        self, *, title: str, performers: tuple[str, ...]
    ) -> list[MetadataCandidate]: ...
