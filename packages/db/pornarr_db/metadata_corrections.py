"""Use explicit administrator corrections before unreliable filename guesses."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.metadata_correction import MetadataCorrection


def normalise_source_title(value: str) -> str:
    return " ".join(value.casefold().split())


async def apply_metadata_correction(
    session: AsyncSession, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Return saved metadata when this source title was corrected previously."""
    source_title = metadata.get("title")
    if not isinstance(source_title, str) or not source_title.strip():
        return metadata
    correction = await session.scalar(
        select(MetadataCorrection).where(
            MetadataCorrection.source_title == normalise_source_title(source_title)
        )
    )
    if correction is None:
        return metadata
    result = dict(metadata)
    result.update(
        {
            "title": correction.title,
            "studio": correction.studio,
            "release_date": correction.release_date.isoformat()
            if correction.release_date is not None
            else None,
            "quality": correction.quality,
        }
    )
    return result


async def store_metadata_correction(
    session: AsyncSession,
    *,
    source_title: str,
    title: str,
    studio: str | None,
    release_date: date | None,
    quality: str | None,
    created_by_id: UUID,
) -> MetadataCorrection:
    """Upsert a correction so the next matching source receives it automatically."""
    normalised = normalise_source_title(source_title)
    correction = await session.scalar(
        select(MetadataCorrection).where(MetadataCorrection.source_title == normalised)
    )
    if correction is None:
        correction = MetadataCorrection(
            source_title=normalised,
            title=title,
            studio=studio,
            release_date=release_date,
            quality=quality,
            created_by_id=created_by_id,
        )
        session.add(correction)
    else:
        correction.title = title
        correction.studio = studio
        correction.release_date = release_date
        correction.quality = quality
        correction.created_by_id = created_by_id
    return correction
