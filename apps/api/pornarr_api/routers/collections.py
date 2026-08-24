"""Per-guest collections — "Guests can build playlists, private to each guest".

Private is the default and the fallback: a collection is invisible to everyone
but its owner until the owner marks it shared, and even an administrator does
not get to browse a private shelf. Administration on this server is over the
library, not over what a guest saved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.authorship import author_name
from pornarr_api.errors import ErrorResponse
from pornarr_api.idempotency import insert_once
from pornarr_api.routers.ratings import media_or_404
from pornarr_db.models.media import Media
from pornarr_db.models.social import Collection, CollectionItem, CollectionVisibility
from pornarr_db.models.user import User
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/collections", tags=["collections"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]

MAXIMUM_PAGE_SIZE = 100


class CollectionDuplicateError(PornarrError):
    code = "COLLECTION_ALREADY_EXISTS"
    status = 409


class CollectionCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    visibility: CollectionVisibility = CollectionVisibility.PRIVATE

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class CollectionUpdate(BaseModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    visibility: CollectionVisibility | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def has_a_change(self) -> CollectionUpdate:
        if self.name is None and self.visibility is None:
            raise ValueError("at least one field must be supplied")
        return self


class CollectionResponse(BaseModel):
    id: UUID
    name: str
    visibility: CollectionVisibility
    owner: str
    is_yours: bool
    item_count: int
    created_at: datetime


class CollectionItemResponse(BaseModel):
    media_id: UUID
    media_title: str
    added_at: datetime


class CollectionDetailResponse(CollectionResponse):
    items: list[CollectionItemResponse]


async def _collection_responses(
    session: AsyncSession, collections: list[Collection], viewer: User
) -> list[CollectionResponse]:
    if not collections:
        return []
    counts = dict(
        (
            await session.execute(
                select(CollectionItem.collection_id, func.count())
                .where(CollectionItem.collection_id.in_([c.id for c in collections]))
                .group_by(CollectionItem.collection_id)
            )
        )
        .tuples()
        .all()
    )
    owners = {
        user.id: author_name(user)
        for user in await session.scalars(
            select(User).where(User.id.in_({c.owner_id for c in collections}))
        )
    }
    return [
        CollectionResponse(
            id=collection.id,
            name=collection.name,
            visibility=collection.visibility,
            owner=owners.get(collection.owner_id, ""),
            is_yours=collection.owner_id == viewer.id,
            item_count=counts.get(collection.id, 0),
            created_at=collection.created_at,
        )
        for collection in collections
    ]


async def readable_or_404(session: AsyncSession, collection_id: UUID, viewer: User) -> Collection:
    """404 rather than 403 for someone else's private shelf.

    A 403 would confirm the collection exists, which is exactly what "private"
    is supposed to withhold.
    """
    collection = await session.scalar(
        select(Collection).where(
            Collection.id == collection_id,
            (Collection.owner_id == viewer.id)
            | (Collection.visibility == CollectionVisibility.SHARED),
        )
    )
    if collection is None:
        raise HTTPException(status_code=404)
    return collection


async def owned_or_404(session: AsyncSession, collection_id: UUID, viewer: User) -> Collection:
    collection = await session.scalar(
        select(Collection).where(Collection.id == collection_id, Collection.owner_id == viewer.id)
    )
    if collection is None:
        raise HTTPException(status_code=404)
    return collection


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    user: CurrentUser, session: Session, shared: bool = False
) -> list[CollectionResponse]:
    """Your shelves by default; `shared=true` adds the ones others opened up."""

    statement = select(Collection).where(Collection.owner_id == user.id)
    if shared:
        statement = select(Collection).where(
            (Collection.owner_id == user.id)
            | (Collection.visibility == CollectionVisibility.SHARED)
        )
    collections = list(await session.scalars(statement.order_by(Collection.name)))
    return await _collection_responses(session, collections, user)


@router.post(
    "",
    response_model=CollectionResponse,
    status_code=201,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_collection(
    payload: CollectionCreate, user: CurrentUser, session: Session
) -> CollectionResponse:
    collection = Collection(owner_id=user.id, name=payload.name, visibility=payload.visibility)
    session.add(collection)
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise CollectionDuplicateError("You already have a collection with that name.") from error
    return (await _collection_responses(session, [collection], user))[0]


@router.get(
    "/{collection_id}",
    response_model=CollectionDetailResponse,
    responses={404: {"model": ErrorResponse}},
)
async def read_collection(
    collection_id: UUID,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Field(ge=1, le=MAXIMUM_PAGE_SIZE)] = 100,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> CollectionDetailResponse:
    collection = await readable_or_404(session, collection_id, user)
    rows = list(
        await session.execute(
            select(CollectionItem.media_id, Media.title, CollectionItem.created_at)
            .join(Media, Media.id == CollectionItem.media_id)
            .where(CollectionItem.collection_id == collection_id)
            .order_by(CollectionItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    summary = (await _collection_responses(session, [collection], user))[0]
    return CollectionDetailResponse(
        **summary.model_dump(),
        items=[
            CollectionItemResponse(media_id=media_id, media_title=title, added_at=added_at)
            for media_id, title, added_at in rows
        ],
    )


@router.patch(
    "/{collection_id}",
    response_model=CollectionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_collection(
    collection_id: UUID, payload: CollectionUpdate, user: CurrentUser, session: Session
) -> CollectionResponse:
    collection = await owned_or_404(session, collection_id, user)
    if payload.name is not None:
        collection.name = payload.name
    if payload.visibility is not None:
        collection.visibility = payload.visibility
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise CollectionDuplicateError("You already have a collection with that name.") from error
    return (await _collection_responses(session, [collection], user))[0]


@router.delete("/{collection_id}", status_code=204, responses={404: {"model": ErrorResponse}})
async def delete_collection(collection_id: UUID, user: CurrentUser, session: Session) -> None:
    await session.delete(await owned_or_404(session, collection_id, user))


@router.put(
    "/{collection_id}/items/{media_id}",
    status_code=204,
    response_class=Response,
    responses={404: {"model": ErrorResponse}},
)
async def add_item(
    collection_id: UUID, media_id: UUID, user: CurrentUser, session: Session
) -> Response:
    """Idempotent: adding a title already on the shelf changes nothing."""

    await owned_or_404(session, collection_id, user)
    await media_or_404(session, media_id)
    await insert_once(session, CollectionItem(collection_id=collection_id, media_id=media_id))
    return Response(status_code=204)


@router.delete(
    "/{collection_id}/items/{media_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def remove_item(
    collection_id: UUID, media_id: UUID, user: CurrentUser, session: Session
) -> None:
    await owned_or_404(session, collection_id, user)
    item = await session.scalar(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection_id, CollectionItem.media_id == media_id
        )
    )
    if item is not None:
        await session.delete(item)
