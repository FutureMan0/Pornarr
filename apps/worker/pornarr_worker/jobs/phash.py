"""Non-destructive perceptual duplicate candidate detection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import DuplicateCandidate, MediaFile
from pornarr_db.models.notification import NotificationKind
from pornarr_db.notifications import notify_administrators
from pornarr_db.session import session_scope
from pornarr_media.phash import hamming_distance, perceptual_hash
from pornarr_shared.jobs import TRANSCODE_QUEUE, enqueue_once, job

MAX_HAMMING_DISTANCE = 8


async def compare_perceptual_hash(session: AsyncSession, redis: Any, media_file: MediaFile) -> int:
    """Record every nearby hash as a reviewable candidate, never a deletion."""
    if media_file.perceptual_hash is None:
        return 0
    candidates = list(
        await session.scalars(
            select(MediaFile).where(
                MediaFile.id != media_file.id,
                MediaFile.perceptual_hash.is_not(None),
                MediaFile.is_missing.is_(False),
            )
        )
    )
    matches = 0
    for candidate in candidates:
        assert candidate.perceptual_hash is not None
        distance = hamming_distance(
            int(media_file.perceptual_hash, 16), int(candidate.perceptual_hash, 16)
        )
        if distance > MAX_HAMMING_DISTANCE:
            continue
        exists = await session.scalar(
            select(DuplicateCandidate.id).where(
                DuplicateCandidate.media_file_id == media_file.id,
                DuplicateCandidate.candidate_file_id == candidate.id,
            )
        )
        if exists is not None:
            continue
        session.add(
            DuplicateCandidate(
                media_file_id=media_file.id,
                candidate_file_id=candidate.id,
                hamming_distance=distance,
            )
        )
        matches += 1
        await notify_administrators(
            session,
            redis,
            kind=NotificationKind.INSTANCE_NOTICE,
            payload={
                "media_file_id": str(media_file.id),
                "candidate_file_id": str(candidate.id),
                "hamming_distance": distance,
            },
        )
    return matches


async def compare_perceptual_hash_job(context: dict[str, Any], media_file_id: str) -> int:
    """Compare an already computed hash in the background worker."""
    async with session_scope() as session:
        media_file = await session.get(MediaFile, UUID(media_file_id))
        if media_file is None:
            return 0
        return await compare_perceptual_hash(session, context["redis"], media_file)


async def generate_perceptual_hash_job(context: dict[str, Any], media_file_id: str) -> int:
    """Compute and compare a hash off the import worker's critical path."""
    async with session_scope() as session:
        media_file = await session.get(MediaFile, UUID(media_file_id))
        if media_file is None or media_file.is_missing:
            return 0
        value = await asyncio.to_thread(perceptual_hash, Path(media_file.path))
        media_file.perceptual_hash = value
        return await compare_perceptual_hash(session, context["redis"], media_file)


PERCEPTUAL_HASH_COMPARISON_JOB = job(compare_perceptual_hash_job)
PERCEPTUAL_HASH_JOB = job(generate_perceptual_hash_job)


async def dispatch_perceptual_hashes(context: dict[str, Any]) -> int:
    """Queue missing hashes nightly without holding up imports or scans."""
    async with session_scope() as session:
        file_ids = list(
            await session.scalars(
                select(MediaFile.id).where(
                    MediaFile.perceptual_hash.is_(None), MediaFile.is_missing.is_(False)
                )
            )
        )
    for file_id in file_ids:
        await enqueue_once(
            context["redis"], PERCEPTUAL_HASH_JOB.name, str(file_id), queue=TRANSCODE_QUEUE
        )
    return len(file_ids)


PERCEPTUAL_HASH_DISPATCH_JOB = job(dispatch_perceptual_hashes)
