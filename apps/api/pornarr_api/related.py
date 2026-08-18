"""Which other titles belong beside this one.

DELIBERATELY NOT THE RECOMMENDER. `/api/recommendations` answers "what should
this person watch next", which depends on everything they have ever watched.
This answers "what is like the thing on screen", which depends only on the two
titles. They are different questions and conflating them produces the familiar
failure where a detail page shows the same six titles no matter what you opened.

The consequence worth stating: this endpoint returns the same list for every
account (modulo what each is allowed to see). That is the correct behaviour for
"related", and it also means the answer says nothing about the person asking.

RANKED BY SHARED SIGNALS, STRONGEST LINK NAMED. A shared performer is a stronger
statement of similarity than a shared studio, which is stronger than one shared
tag, because a studio may hold thousands of titles while a performer holds
dozens. The weights below encode that and nothing more subtle; a title matching
on several signals accumulates them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from pornarr_db.models.entities import MediaPerformer, MediaTag
from pornarr_db.models.media import Media, MediaFile

# Per shared item, not per category: three shared tags say more than one.
PERFORMER_WEIGHT = 3.0
TAG_WEIGHT = 1.0
# Once, however many titles the studio has — sharing a studio is one fact.
STUDIO_WEIGHT = 1.5

# Below this a "related" title is a coincidence. One shared tag on its own is
# exactly the case this excludes: almost every title shares one tag with almost
# every other, and a row full of those is worse than an empty row.
MINIMUM_SCORE = 1.5


@dataclass(frozen=True, slots=True)
class Related:
    """One neighbour, with the reason it is one."""

    media_id: UUID
    title: str
    studio: str | None
    duration_seconds: float | None
    score: float
    # The single strongest link, which is what the design's chip shows. Listing
    # every link would make the chip a sentence nobody reads.
    reason: str
    shared_performers: int
    shared_tags: int


async def _shared_counts(
    session: AsyncSession,
    *,
    media_id: UUID,
    link_table: type[MediaTag] | type[MediaPerformer],
    column: InstrumentedAttribute[UUID],
    scope: ColumnElement[bool] | None,
) -> dict[UUID, int]:
    """How many of this title's tags (or performers) each other title shares.

    One query per signal rather than one join across both: a title with 40 tags
    and 5 performers would otherwise produce 200 rows per neighbour and the
    counts would have to be divided back out.
    """
    mine = select(column).where(link_table.media_id == media_id).scalar_subquery()

    statement: Select[tuple[UUID, int]] = (
        select(link_table.media_id, func.count(func.distinct(column)))
        .where(column.in_(mine), link_table.media_id != media_id)
        .group_by(link_table.media_id)
    )
    if scope is not None:
        statement = statement.join(Media, Media.id == link_table.media_id).where(scope)

    rows = (await session.execute(statement)).tuples()
    return dict(rows.all())


async def related_titles(
    session: AsyncSession,
    *,
    media: Media,
    scope: ColumnElement[bool] | None,
    limit: int,
) -> list[Related]:
    """The neighbours of one title, best first."""
    performers = await _shared_counts(
        session,
        media_id=media.id,
        link_table=MediaPerformer,
        column=MediaPerformer.performer_id,
        scope=scope,
    )
    tags = await _shared_counts(
        session,
        media_id=media.id,
        link_table=MediaTag,
        column=MediaTag.tag_id,
        scope=scope,
    )

    same_studio: set[UUID] = set()
    if media.studio is not None:
        studio_statement = select(Media.id).where(
            Media.studio == media.studio, Media.id != media.id
        )
        if scope is not None:
            studio_statement = studio_statement.where(scope)
        same_studio = set((await session.scalars(studio_statement)).all())

    scores: dict[UUID, float] = {}
    for candidate, count in performers.items():
        scores[candidate] = scores.get(candidate, 0.0) + count * PERFORMER_WEIGHT
    for candidate, count in tags.items():
        scores[candidate] = scores.get(candidate, 0.0) + count * TAG_WEIGHT
    for candidate in same_studio:
        scores[candidate] = scores.get(candidate, 0.0) + STUDIO_WEIGHT

    qualifying = {candidate: score for candidate, score in scores.items() if score >= MINIMUM_SCORE}
    if not qualifying:
        return []

    # Ranked before the fetch, so a library with thousands of loosely related
    # titles still reads exactly `limit` rows.
    ranked = sorted(qualifying.items(), key=lambda item: (-item[1], str(item[0])))[:limit]
    wanted = [candidate for candidate, _ in ranked]

    # Duration lives on the file, not the title, and a title can have several.
    # The longest is the one a person means by "how long is this".
    detail = (
        await session.execute(
            select(Media, func.max(MediaFile.duration_seconds))
            .outerjoin(MediaFile, MediaFile.media_id == Media.id)
            .where(Media.id.in_(wanted))
            .group_by(Media.id)
        )
    ).all()
    rows = {neighbour.id: (neighbour, duration) for neighbour, duration in detail}

    results: list[Related] = []
    for candidate, score in ranked:
        found = rows.get(candidate)
        if found is None:
            continue
        row, duration = found
        results.append(
            Related(
                media_id=candidate,
                title=row.title,
                studio=row.studio,
                duration_seconds=duration,
                score=score,
                reason=_reason(
                    performers=performers.get(candidate, 0),
                    tags=tags.get(candidate, 0),
                    same_studio=candidate in same_studio,
                ),
                shared_performers=performers.get(candidate, 0),
                shared_tags=tags.get(candidate, 0),
            )
        )
    return results


def _reason(*, performers: int, tags: int, same_studio: bool) -> str:
    """The strongest link, as a key the client turns into a sentence.

    A key rather than English: this is the one place a reason is decided, and
    the interface is translated. The keys are stable and the client owns the
    wording.
    """
    if performers > 0:
        return "performer"
    if same_studio and tags * TAG_WEIGHT < STUDIO_WEIGHT:
        return "studio"
    if tags > 0:
        return "tag"
    return "studio"
