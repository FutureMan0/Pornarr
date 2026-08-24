"""Persist the scene index produced by the preview pass.

The detection itself is free here — it happened during the decode the preview
already paid for. This module only writes the result down.

Replacing an index is a delete-then-insert rather than a merge. Re-running
detection means the thresholds or the file changed, and reconciling old
ordinals against new boundaries would produce an index that matches neither.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.session import session_scope
from pornarr_media.scenes import Scene, SceneDetectionOptions
from pornarr_media.sprites import PreviewSpriteOptions, try_generate_preview_and_scenes
from pornarr_shared.jobs import job


async def replace_scene_markers(
    session: AsyncSession, media_file_id: UUID, scenes: tuple[Scene, ...]
) -> int:
    """Swap in a fresh index for one file, inside the caller's transaction."""
    await session.execute(delete(SceneMarker).where(SceneMarker.media_file_id == media_file_id))
    session.add_all(
        SceneMarker(
            media_file_id=media_file_id,
            ordinal=scene.ordinal,
            start_seconds=scene.start_seconds,
            end_seconds=scene.end_seconds,
        )
        for scene in scenes
    )
    return len(scenes)


async def generate_preview_and_scenes_job(
    _: dict[str, Any],
    media_file_id: str,
    source_path: str,
    output_directory: str,
    *,
    interval_seconds: float = 10,
    tile_width: int = 160,
    tile_height: int = 90,
    columns: int = 10,
    scene_threshold: float = SceneDetectionOptions().threshold,
    scene_analysis_fps: float = SceneDetectionOptions().analysis_fps,
    minimum_scene_seconds: float = SceneDetectionOptions().minimum_scene_seconds,
) -> int:
    """One decode, a preview sprite and a persisted scene index.

    Returns the number of markers written; zero also covers the case where the
    pass failed, because neither a preview nor a scene index is worth failing
    an import over.
    """
    result = await asyncio.to_thread(
        try_generate_preview_and_scenes,
        Path(source_path),
        Path(output_directory),
        options=PreviewSpriteOptions(
            interval_seconds=interval_seconds,
            tile_width=tile_width,
            tile_height=tile_height,
            columns=columns,
        ),
        scene_options=SceneDetectionOptions(
            threshold=scene_threshold,
            analysis_fps=scene_analysis_fps,
            minimum_scene_seconds=minimum_scene_seconds,
        ),
    )
    if result is None:
        return 0
    async with session_scope() as session:
        return await replace_scene_markers(session, UUID(media_file_id), result.scenes)


PREVIEW_AND_SCENES_JOB = job(generate_preview_and_scenes_job)
