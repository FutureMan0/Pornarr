"""Where a library file is allowed to live."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.root_folders import RootFolder


async def library_roots(session: AsyncSession, library_path: Path) -> tuple[Path, ...]:
    """Every directory the library's own files may sit under.

    An import lands in the enabled root folder when there is one and in
    `library_path` only when there is not (`jobs/import_media.py`), and the
    scanner adopts whatever those roots already hold. A playback route that
    trusted `library_path` alone therefore refused every title on an instance
    whose root folder is anywhere else - which is every instance that was given
    a library path in the setup wizard.
    """
    configured = await session.scalars(select(RootFolder.path).where(RootFolder.enabled.is_(True)))
    return (library_path.resolve(), *(Path(path).resolve() for path in configured))


def file_within(path: str, roots: tuple[Path, ...]) -> Path | None:
    """The resolved file, or None when it is not a file inside one of `roots`."""
    candidate = Path(path).resolve()
    if not any(candidate.is_relative_to(root) for root in roots) or not candidate.is_file():
        return None
    return candidate
