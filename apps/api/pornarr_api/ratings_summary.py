"""Aggregate ratings for a page of titles, in one query.

The library, local search and the shorts feed all want an average and a count
beside each title. Fetching them per row is the obvious way to turn a
forty-eight item page into forty-nine queries, so every caller loads the whole
page's aggregates at once through here.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.social import Rating


async def rating_summaries(
    session: AsyncSession, media_ids: Iterable[UUID]
) -> dict[UUID, tuple[float, int]]:
    """Average and count per title, omitting titles nobody has rated."""
    ids = list(media_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Rating.media_id, func.avg(Rating.stars), func.count())
            .where(Rating.media_id.in_(ids))
            .group_by(Rating.media_id)
        )
    ).tuples()
    return {media_id: (round(float(average), 2), count) for media_id, average, count in rows}


def rating_filter(minimum_stars: float):
    """A subquery predicate for `rating_gte`, joined into the caller's statement."""
    return (
        select(Rating.media_id)
        .group_by(Rating.media_id)
        .having(func.avg(Rating.stars) >= minimum_stars)
    )
