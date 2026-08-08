"""Authenticated byte-range streaming of imported media files."""

from __future__ import annotations

import mimetypes
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, get_current_user
from pornarr_api.errors import ErrorResponse, error_body
from pornarr_db.models.media import MediaFile
from pornarr_db.models.user import User

router = APIRouter(prefix="/media", tags=["playback"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(database_session)]
_CHUNK_SIZE = 64 * 1024


class RangeNotSatisfiableError(ValueError):
    """The requested byte range cannot select data from the file."""


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_byte_ranges(header: str | None, size: int) -> tuple[ByteRange, ...]:
    """Parse a strict RFC 9110 byte-range header into inclusive offsets."""
    if header is None:
        return ()
    unit, separator, values = header.partition("=")
    if unit.strip().lower() != "bytes" or separator != "=" or not values.strip() or size <= 0:
        raise RangeNotSatisfiableError

    ranges: list[ByteRange] = []
    for value in values.split(","):
        first, dash, last = value.strip().partition("-")
        if dash != "-" or not (first or last) or (first and not first.isdecimal()):
            raise RangeNotSatisfiableError
        if not first:
            if not last.isdecimal() or int(last) <= 0:
                raise RangeNotSatisfiableError
            start, end = max(0, size - int(last)), size - 1
        else:
            start = int(first)
            if start >= size or (last and not last.isdecimal()):
                raise RangeNotSatisfiableError
            end = min(int(last), size - 1) if last else size - 1
            if end < start:
                raise RangeNotSatisfiableError
        ranges.append(ByteRange(start, end))
    return tuple(ranges)


def _library_file(path: str, library_path: Path) -> Path | None:
    candidate = Path(path).resolve()
    root = library_path.resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _read_range(path: Path, byte_range: ByteRange) -> Iterator[bytes]:
    remaining = byte_range.length
    with path.open("rb") as file:
        file.seek(byte_range.start)
        while remaining:
            chunk = file.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def _multipart_body(
    path: Path, ranges: tuple[ByteRange, ...], size: int, media_type: str, boundary: str
) -> Iterator[bytes]:
    for byte_range in ranges:
        yield _part_header(byte_range, size, media_type, boundary)
        yield from _read_range(path, byte_range)
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


def _part_header(byte_range: ByteRange, size: int, media_type: str, boundary: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f"Content-Type: {media_type}\r\n"
        f"Content-Range: bytes {byte_range.start}-{byte_range.end}/{size}\r\n\r\n"
    ).encode()


def _multipart_length(
    ranges: tuple[ByteRange, ...], size: int, media_type: str, boundary: str
) -> int:
    return sum(
        len(_part_header(byte_range, size, media_type, boundary)) + byte_range.length + 2
        for byte_range in ranges
    ) + len(f"--{boundary}--\r\n".encode())


def _stream_response(path: Path, ranges: tuple[ByteRange, ...], size: int) -> StreamingResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes"}
    if not ranges:
        headers["Content-Length"] = str(size)
        body = iter(()) if size == 0 else _read_range(path, ByteRange(0, size - 1))
        return StreamingResponse(body, media_type=media_type, headers=headers)
    if len(ranges) == 1:
        byte_range = ranges[0]
        headers.update(
            {
                "Content-Length": str(byte_range.length),
                "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{size}",
            }
        )
        return StreamingResponse(
            _read_range(path, byte_range), status_code=206, media_type=media_type, headers=headers
        )
    boundary = secrets.token_hex(16)
    headers.update(
        {
            "Content-Length": str(_multipart_length(ranges, size, media_type, boundary)),
            "Content-Type": f"multipart/byteranges; boundary={boundary}",
        }
    )
    return StreamingResponse(
        _multipart_body(path, ranges, size, media_type, boundary), status_code=206, headers=headers
    )


@router.get(
    "/{media_id}/stream",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        416: {"model": ErrorResponse},
    },
)
async def stream(media_id: UUID, request: Request, _: CurrentUser, session: Session) -> Response:
    media_file = await session.scalar(
        select(MediaFile).where(MediaFile.media_id == media_id, MediaFile.is_active.is_(True))
    )
    if media_file is None:
        raise HTTPException(status_code=404)
    path = _library_file(media_file.path, request.app.state.settings.library_path)
    if path is None:
        raise HTTPException(status_code=404)
    size = path.stat().st_size
    try:
        ranges = parse_byte_ranges(request.headers.get("Range"), size)
    except RangeNotSatisfiableError:
        return JSONResponse(
            status_code=416,
            content=error_body("RANGE_NOT_SATISFIABLE", 416),
            headers={"Content-Range": f"bytes */{size}"},
        )
    return _stream_response(path, ranges, size)
