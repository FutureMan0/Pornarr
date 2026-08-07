"""Repository base.

Repositories take a session rather than opening one. Whoever owns the unit of
work — a request handler, a job — decides the transaction boundary; a repository
that commits on its own makes that impossible to control from outside.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.base import Base


class Repository[ModelT: Base]:
    """Read and write access for one model."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: Any) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    # Deliberately not called `list`: a method of that name shadows the builtin
    # inside the class body, so `-> list[ModelT]` would annotate the method
    # itself rather than the type.
    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        result = await self.session.scalars(select(self.model).limit(limit).offset(offset))
        return list(result)

    async def count(self) -> int:
        total = await self.session.scalar(select(func.count()).select_from(self.model))
        return total or 0

    def add(self, entity: ModelT) -> ModelT:
        """Stage an insert. Not committed: the caller owns the transaction."""
        self.session.add(entity)
        return entity

    async def delete(self, entity_id: Any) -> int:
        """Delete by primary key, returning how many rows went.

        The caller needs the count to tell 'deleted' from 'was not there', which
        are different outcomes for an idempotent job.
        """
        # DELETE always yields a cursor result; `execute` is annotated more
        # broadly. A cast rather than an assert, which `python -O` strips.
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                delete(self.model).where(self.model.__table__.c.id == entity_id)
            ),
        )
        return result.rowcount
