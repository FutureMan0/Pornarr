"""Background jobs for poster and preview-frame regeneration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from pornarr_media.artwork import ArtworkState, generate_artwork
from pornarr_shared.jobs import job


async def generate_artwork_job(
    _: dict[str, Any],
    source_path: str,
    thumbnail_path: str,
    media_id: str,
    *,
    preview_frame_count: int = 4,
) -> bool:
    """Regenerate one item's artwork away from the import worker."""
    artwork = await asyncio.to_thread(
        generate_artwork,
        Path(source_path),
        Path(thumbnail_path),
        UUID(media_id),
        preview_frame_count=preview_frame_count,
    )
    return artwork.state == ArtworkState.READY


async def regenerate_library_artwork_job(
    context: dict[str, Any],
    thumbnail_path: str,
    items: list[dict[str, str]],
    *,
    preview_frame_count: int = 4,
) -> int:
    """Regenerate all requested items, reporting how many yielded real artwork."""
    generated = 0
    for item in items:
        if await generate_artwork_job(
            context,
            item["source_path"],
            thumbnail_path,
            item["media_id"],
            preview_frame_count=preview_frame_count,
        ):
            generated += 1
    return generated


ARTWORK_JOB = job(generate_artwork_job)
LIBRARY_ARTWORK_JOB = job(regenerate_library_artwork_job)
