"""Entity-assignment operations that need database coordination."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import MediaPerformer, MediaTag, Performer


async def merge_performers(session: AsyncSession, source_id: UUID, target_id: UUID) -> None:
    """Move every unique media assignment to the canonical performer, then remove the duplicate."""
    source_media_ids = set(
        await session.scalars(
            select(MediaPerformer.media_id).where(MediaPerformer.performer_id == source_id)
        )
    )
    target_media_ids = set(
        await session.scalars(
            select(MediaPerformer.media_id).where(MediaPerformer.performer_id == target_id)
        )
    )
    if source_media_ids - target_media_ids:
        await session.execute(
            update(MediaPerformer)
            .where(
                MediaPerformer.performer_id == source_id,
                MediaPerformer.media_id.in_(source_media_ids - target_media_ids),
            )
            .values(performer_id=target_id)
        )
    if source_media_ids & target_media_ids:
        await session.execute(
            delete(MediaPerformer).where(
                MediaPerformer.performer_id == source_id,
                MediaPerformer.media_id.in_(source_media_ids & target_media_ids),
            )
        )
    await session.execute(delete(Performer).where(Performer.id == source_id))


def preferred_tag_assignment(assignments: Iterable[MediaTag]) -> MediaTag | None:
    """Select the highest-authority assignment for a tag; manual corrections win."""
    candidates = tuple(assignments)
    if not candidates:
        return None
    return max(candidates, key=lambda assignment: assignment.source == "user")
