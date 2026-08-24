"""Read/write operations for the expiring external-release cache."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.release import ReleaseCache


def normalize_release_title(title: str) -> str:
    """Make title lookups stable across punctuation and whitespace changes."""
    return " ".join(re.sub(r"[^\w]+", " ", title.casefold()).split())


class ReleaseCacheRepository:
    """Hide cache replacement, expiry and title lookup behind one module."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, release: ReleaseCache) -> ReleaseCache:
        existing = await self.session.scalar(
            select(ReleaseCache).where(
                ReleaseCache.indexer_id == release.indexer_id,
                ReleaseCache.guid == release.guid,
            )
        )
        if existing is None:
            self.session.add(release)
            return release
        for field, value in _values(release).items():
            setattr(existing, field, value)
        return existing

    async def search(self, query: str, *, now: datetime | None = None) -> list[ReleaseCache]:
        term = normalize_release_title(query)
        if not term:
            return []
        current = now or datetime.now(UTC)
        return list(
            await self.session.scalars(
                select(ReleaseCache)
                .where(
                    ReleaseCache.expires_at > current,
                    ReleaseCache.normalized_title.contains(term),
                )
                .order_by(ReleaseCache.published_at.desc())
            )
        )

    async def expire(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                delete(ReleaseCache).where(ReleaseCache.expires_at <= current)
            ),
        )
        return result.rowcount


def _values(release: ReleaseCache) -> dict[str, Any]:
    return {
        column.name: getattr(release, column.name)
        for column in ReleaseCache.__table__.columns
        if column.name not in {"id", "indexer_id", "guid", "created_at", "updated_at"}
    }
