"""Comments on a title, their likes, and the administrator's moderation queue.

Visibility follows the design's privacy note: comments are visible to everyone
signed in to the server. What is *not* shared is who reported what — a report
is only ever visible to an administrator, so flagging a neighbour's remark does
not turn into a household argument.

Each comment carries its author's rating for the same title, because the
mockups show stars beside every remark and fetching them separately would mean
one query per comment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user, require_role
from pornarr_api.authorship import author_name
from pornarr_api.errors import ErrorResponse
from pornarr_api.idempotency import insert_once
from pornarr_api.routers.ratings import media_or_404
from pornarr_db.models.media import Media
from pornarr_db.models.social import Comment, CommentLike, CommentReport, CommentState, Rating
from pornarr_db.models.user import User, UserRole

router = APIRouter(tags=["comments"])
admin_router = APIRouter(prefix="/admin/comments", tags=["admin"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

MAXIMUM_BODY = 4000
MAXIMUM_PAGE_SIZE = 100


class CommentWrite(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=MAXIMUM_BODY)]

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class CommentReportWrite(BaseModel):
    reason: Annotated[str | None, Field(max_length=512)] = None

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        stripped = (value or "").strip()
        return stripped or None


class CommentStateWrite(BaseModel):
    state: CommentState


class CommentResponse(BaseModel):
    id: UUID
    media_id: UUID
    author: str
    body: str
    state: CommentState
    stars: int | None
    likes: int
    you_liked: bool
    is_yours: bool
    created_at: datetime
    edited_at: datetime | None


class ModeratedCommentResponse(CommentResponse):
    media_title: str
    reports: int


def _like_count() -> Select[tuple[UUID, int]]:
    return select(CommentLike.comment_id, func.count().label("likes")).group_by(
        CommentLike.comment_id
    )


async def _decorate(
    session: AsyncSession, comments: list[Comment], viewer: User
) -> dict[UUID, tuple[int, bool, int | None, str]]:
    """Likes, your-like, the author's own stars and the author label, in bulk.

    Three grouped queries rather than four per comment: a busy title has
    hundreds of remarks and the detail screen loads them all at once.
    """
    if not comments:
        return {}
    ids = [comment.id for comment in comments]
    likes = dict(
        (await session.execute(_like_count().where(CommentLike.comment_id.in_(ids)))).tuples().all()
    )
    liked_by_viewer = set(
        await session.scalars(
            select(CommentLike.comment_id).where(
                CommentLike.comment_id.in_(ids), CommentLike.user_id == viewer.id
            )
        )
    )
    author_ids = {comment.user_id for comment in comments}
    media_ids = {comment.media_id for comment in comments}
    stars = {
        (user_id, media_id): value
        for user_id, media_id, value in await session.execute(
            select(Rating.user_id, Rating.media_id, Rating.stars).where(
                Rating.user_id.in_(author_ids), Rating.media_id.in_(media_ids)
            )
        )
    }
    authors = {
        user.id: author_name(user)
        for user in await session.scalars(select(User).where(User.id.in_(author_ids)))
    }
    return {
        comment.id: (
            likes.get(comment.id, 0),
            comment.id in liked_by_viewer,
            stars.get((comment.user_id, comment.media_id)),
            authors.get(comment.user_id, ""),
        )
        for comment in comments
    }


def comment_response(
    comment: Comment, viewer: User, extra: tuple[int, bool, int | None, str]
) -> CommentResponse:
    likes, you_liked, stars, author = extra
    return CommentResponse(
        id=comment.id,
        media_id=comment.media_id,
        author=author,
        body=comment.body,
        state=comment.state,
        stars=stars,
        likes=likes,
        you_liked=you_liked,
        is_yours=comment.user_id == viewer.id,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
    )


async def comment_or_404(session: AsyncSession, comment_id: UUID) -> Comment:
    comment = await session.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404)
    return comment


@router.get(
    "/media/{media_id}/comments",
    response_model=list[CommentResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_comments(
    media_id: UUID,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[CommentResponse]:
    """Hidden comments stay visible to their author and to administrators.

    Silently vanishing a remark teaches the author nothing; the state is part of
    the response so the client can mark it as withheld.
    """
    await media_or_404(session, media_id)
    statement = select(Comment).where(Comment.media_id == media_id)
    if user.role is not UserRole.ADMIN:
        statement = statement.where(
            (Comment.state != CommentState.HIDDEN) | (Comment.user_id == user.id)
        )
    comments = list(
        await session.scalars(
            statement.order_by(Comment.created_at.desc()).limit(limit).offset(offset)
        )
    )
    extras = await _decorate(session, comments, user)
    return [comment_response(comment, user, extras[comment.id]) for comment in comments]


@router.post(
    "/media/{media_id}/comments",
    response_model=CommentResponse,
    status_code=201,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_comment(
    media_id: UUID, payload: CommentWrite, user: CurrentUser, session: Session
) -> CommentResponse:
    await media_or_404(session, media_id)
    comment = Comment(user_id=user.id, media_id=media_id, body=payload.body)
    session.add(comment)
    await session.flush()
    extras = await _decorate(session, [comment], user)
    return comment_response(comment, user, extras[comment.id])


@router.patch(
    "/comments/{comment_id}",
    response_model=CommentResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def edit_comment(
    comment_id: UUID, payload: CommentWrite, user: CurrentUser, session: Session
) -> CommentResponse:
    """Only the author edits. An administrator can hide or delete, never rewrite.

    Putting words in someone's mouth is a different power from moderation, and
    the audit trail would not show it.
    """
    comment = await comment_or_404(session, comment_id)
    if comment.user_id != user.id:
        raise HTTPException(status_code=403)
    comment.body = payload.body
    comment.edited_at = datetime.now(UTC)
    await session.flush()
    extras = await _decorate(session, [comment], user)
    return comment_response(comment, user, extras[comment.id])


@router.delete(
    "/comments/{comment_id}",
    status_code=204,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_comment(comment_id: UUID, user: CurrentUser, session: Session) -> None:
    comment = await comment_or_404(session, comment_id)
    if comment.user_id != user.id and user.role is not UserRole.ADMIN:
        raise HTTPException(status_code=403)
    await session.delete(comment)


@router.put(
    "/comments/{comment_id}/like",
    status_code=204,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
async def like_comment(comment_id: UUID, user: CurrentUser, session: Session) -> Response:
    """Idempotent: a double-tap must not become a second like."""

    await comment_or_404(session, comment_id)
    await insert_once(session, CommentLike(comment_id=comment_id, user_id=user.id))
    return Response(status_code=204)


@router.delete(
    "/comments/{comment_id}/like", status_code=204, responses={404: {"model": ErrorResponse}}
)
async def unlike_comment(comment_id: UUID, user: CurrentUser, session: Session) -> None:
    await comment_or_404(session, comment_id)
    like = await session.scalar(
        select(CommentLike).where(
            CommentLike.comment_id == comment_id, CommentLike.user_id == user.id
        )
    )
    if like is not None:
        await session.delete(like)


@router.post(
    "/comments/{comment_id}/report",
    status_code=202,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
async def report_comment(
    comment_id: UUID, payload: CommentReportWrite, user: CurrentUser, session: Session
) -> Response:
    """202, and never a count: the reporter learns nothing about other reports.

    Reporting twice updates the reason rather than stacking, so a single reader
    cannot make a remark look widely objected to.
    """
    await comment_or_404(session, comment_id)
    report = CommentReport(comment_id=comment_id, reporter_id=user.id, reason=payload.reason)
    if not await insert_once(session, report):
        existing = await session.scalar(
            select(CommentReport).where(
                CommentReport.comment_id == comment_id, CommentReport.reporter_id == user.id
            )
        )
        if existing is not None:
            existing.reason = payload.reason
        await session.flush()
    return Response(status_code=202)


@admin_router.get("", response_model=list[ModeratedCommentResponse])
async def list_moderation_queue(
    admin: Admin,
    session: Session,
    reported_only: bool = False,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> list[ModeratedCommentResponse]:
    """Reported first, then newest — the order the A6 screen reads in."""

    reports = (
        select(CommentReport.comment_id, func.count().label("reports"))
        .group_by(CommentReport.comment_id)
        .subquery()
    )
    statement = (
        select(Comment, Media.title, func.coalesce(reports.c.reports, 0))
        .join(Media, Media.id == Comment.media_id)
        .outerjoin(reports, reports.c.comment_id == Comment.id)
    )
    if reported_only:
        statement = statement.where(reports.c.reports > 0)
    rows = list(
        await session.execute(
            statement.order_by(
                func.coalesce(reports.c.reports, 0).desc(), Comment.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    )
    comments = [row[0] for row in rows]
    extras = await _decorate(session, comments, admin)
    return [
        ModeratedCommentResponse(
            **comment_response(comment, admin, extras[comment.id]).model_dump(),
            media_title=title,
            reports=report_count,
        )
        for comment, title, report_count in rows
    ]


@admin_router.patch(
    "/{comment_id}",
    response_model=ModeratedCommentResponse,
    responses={404: {"model": ErrorResponse}},
)
async def set_comment_state(
    comment_id: UUID, payload: CommentStateWrite, admin: Admin, session: Session
) -> ModeratedCommentResponse:
    """Resolving a comment clears its reports: the queue must be able to empty."""

    comment = await comment_or_404(session, comment_id)
    comment.state = payload.state
    if payload.state is not CommentState.OPEN:
        for report in await session.scalars(
            select(CommentReport).where(CommentReport.comment_id == comment_id)
        ):
            await session.delete(report)
    await session.flush()
    media = await session.get(Media, comment.media_id)
    extras = await _decorate(session, [comment], admin)
    return ModeratedCommentResponse(
        **comment_response(comment, admin, extras[comment.id]).model_dump(),
        media_title=media.title if media is not None else "",
        reports=0,
    )
