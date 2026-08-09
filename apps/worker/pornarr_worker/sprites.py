"""Background jobs for non-essential media preview assets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pornarr_media.sprites import PreviewSpriteOptions, try_generate_preview_sprite
from pornarr_shared.jobs import job


async def generate_preview_sprite_job(
    _: dict[str, Any],
    source_path: str,
    output_directory: str,
    *,
    interval_seconds: float = 10,
    tile_width: int = 160,
    tile_height: int = 90,
    columns: int = 10,
) -> bool:
    """Generate a preview without allowing its failure to fail an import."""
    preview = await asyncio.to_thread(
        try_generate_preview_sprite,
        Path(source_path),
        Path(output_directory),
        options=PreviewSpriteOptions(
            interval_seconds=interval_seconds,
            tile_width=tile_width,
            tile_height=tile_height,
            columns=columns,
        ),
    )
    return preview is not None


SPRITE_JOB = job(generate_preview_sprite_job)
