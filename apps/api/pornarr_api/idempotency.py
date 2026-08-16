"""Insert-once helpers that survive a second click and a second tab.

Deliberately not `ON CONFLICT`: the API runs on PostgreSQL but the unit suite
runs on SQLite, and the dialect-specific `insert` construct is not portable
between them. A SAVEPOINT costs one round trip and behaves the same on both, so
the tests exercise the same code path production does.

The savepoint is the point: catching `IntegrityError` without one aborts the
whole transaction, which would silently discard everything else the request had
already written.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.base import Base


async def insert_once(session: AsyncSession, instance: Base) -> bool:
    """Add a row unless an equal one already exists.

    Returns whether this call was the one that inserted it, so a caller that
    needs to distinguish "created" from "already there" still can.
    """
    try:
        async with session.begin_nested():
            session.add(instance)
            await session.flush()
    except IntegrityError:
        return False
    return True
