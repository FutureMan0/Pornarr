"""A4 — the tag list, and the three ways to clean it up.

A library accumulates tags the way a drawer accumulates cables: a scan invents
one, a scraper invents a near-duplicate, and after a year "low-light",
"lowlight" and "low light" all exist and none of them finds everything. So the
useful operations are not "create" — nothing here creates a tag, the importers
do — but rename, merge and delete.

MERGE IS THE INTERESTING ONE. Repointing every assignment is easy; the trap is
the title that already carries both tags, where a naive repoint violates the
uniqueness of (media, tag, source) and takes the whole merge down with it. Those
rows are dropped rather than moved, because the title already has the tag the
merge is moving towards.

WHAT IS NOT HERE. The design shows a group per tag and a guest-visible flag.
Neither exists in the schema, and adding a column to satisfy a screenshot would
put a control on screen that governs nothing. They are absent rather than
decorative.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.entities import MediaTag, Tag
from pornarr_db.models.user import User, UserRole

router = APIRouter(prefix="/admin/tags", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


def normalize(name: str) -> str:
    """The form uniqueness is decided on. Matches what the importers write."""
    return name.strip().casefold()


class TagResponse(BaseModel):
    id: UUID
    name: str
    # How many titles carry it. A tag on nothing is the first candidate for
    # deletion, which is why zero is reported rather than omitted.
    media_count: int
    # When it was last attached to anything, not when the tag row was made. A
    # tag nobody has used in a year is the second candidate.
    last_used_at: datetime | None


class TagRename(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TagMerge(BaseModel):
    """Fold `source_ids` into this tag."""

    source_ids: Annotated[list[UUID], Field(min_length=1, max_length=64)]


@router.get("", response_model=list[TagResponse])
async def list_tags(
    _: Admin,
    session: Session,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[TagResponse]:
    """Every tag with its weight, commonest first.

    Ordered by use rather than alphabetically: the reason to open this screen is
    almost always to deal with the long tail, and a list sorted by name buries
    it among the tags that are working fine.
    """
    usage = (
        select(
            MediaTag.tag_id,
            func.count(func.distinct(MediaTag.media_id)).label("media_count"),
            func.max(MediaTag.created_at).label("last_used_at"),
        )
        .group_by(MediaTag.tag_id)
        .subquery()
    )

    statement = (
        select(
            Tag.id,
            Tag.name,
            func.coalesce(usage.c.media_count, 0),
            usage.c.last_used_at,
        )
        .outerjoin(usage, usage.c.tag_id == Tag.id)
        .order_by(func.coalesce(usage.c.media_count, 0).desc(), Tag.name)
        .limit(limit)
    )
    if q is not None and q.strip():
        statement = statement.where(Tag.normalized_name.contains(normalize(q)))

    return [
        TagResponse(id=tag_id, name=name, media_count=int(count), last_used_at=last_used)
        for tag_id, name, count, last_used in (await session.execute(statement)).tuples()
    ]


async def _tag_or_404(session: AsyncSession, tag_id: UUID) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404)
    return tag


@router.patch("/{tag_id}", response_model=TagResponse)
async def rename_tag(
    tag_id: UUID, payload: TagRename, admin: Admin, session: Session
) -> TagResponse:
    tag = await _tag_or_404(session, tag_id)
    normalized = normalize(payload.name)

    clash = await session.scalar(
        select(Tag).where(Tag.normalized_name == normalized, Tag.id != tag_id)
    )
    if clash is not None:
        # Renaming onto an existing tag is a merge, and the caller should say so:
        # doing it silently would fold two tags together on a typo.
        raise HTTPException(status_code=409, detail="A tag with that name already exists.")

    previous = tag.name
    tag.name = payload.name
    tag.normalized_name = normalized
    write_audit(
        session,
        actor_id=admin.id,
        action="tag.renamed",
        target=str(tag_id),
        context={"from": previous, "to": payload.name},
    )
    await session.flush()
    return await _one(session, tag_id)


@router.post("/{tag_id}/merge", response_model=TagResponse)
async def merge_tags(
    tag_id: UUID, payload: TagMerge, admin: Admin, session: Session
) -> TagResponse:
    """Fold other tags into this one, then remove them."""
    await _tag_or_404(session, tag_id)
    sources = [source for source in payload.source_ids if source != tag_id]
    if not sources:
        raise HTTPException(status_code=422, detail="A tag cannot be merged into itself.")

    # Which (media, source) pairs the target already covers. Moving those would
    # collide on the unique index and abort the whole merge.
    already = {
        (media_id, source)
        for media_id, source in (
            await session.execute(
                select(MediaTag.media_id, MediaTag.source).where(MediaTag.tag_id == tag_id)
            )
        ).tuples()
    }

    moved = 0
    for assignment in await session.scalars(select(MediaTag).where(MediaTag.tag_id.in_(sources))):
        if (assignment.media_id, assignment.source) in already:
            await session.delete(assignment)
            continue
        assignment.tag_id = tag_id
        already.add((assignment.media_id, assignment.source))
        moved += 1

    await session.flush()
    await session.execute(delete(Tag).where(Tag.id.in_(sources)))
    write_audit(
        session,
        actor_id=admin.id,
        action="tag.merged",
        target=str(tag_id),
        context={"sources": [str(source) for source in sources], "moved": moved},
    )
    await session.flush()
    return await _one(session, tag_id)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: UUID, admin: Admin, session: Session) -> None:
    """Remove a tag and every assignment of it.

    No confirmation here — that belongs to the interface. What this does
    guarantee is that no title is left pointing at a tag that no longer exists.
    """
    tag = await _tag_or_404(session, tag_id)
    name = tag.name
    await session.execute(delete(MediaTag).where(MediaTag.tag_id == tag_id))
    await session.delete(tag)
    write_audit(
        session, actor_id=admin.id, action="tag.deleted", target=str(tag_id), context={"name": name}
    )
    await session.flush()


async def _one(session: AsyncSession, tag_id: UUID) -> TagResponse:
    """Re-read one tag with its counts, so a write answers with the new truth."""
    row = (
        await session.execute(
            select(
                Tag.id,
                Tag.name,
                func.count(func.distinct(MediaTag.media_id)),
                func.max(MediaTag.created_at),
            )
            .outerjoin(MediaTag, MediaTag.tag_id == Tag.id)
            .where(Tag.id == tag_id)
            .group_by(Tag.id, Tag.name)
        )
    ).one()
    return TagResponse(id=row[0], name=row[1], media_count=int(row[2]), last_used_at=row[3])
