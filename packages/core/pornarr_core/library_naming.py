"""Safe, deterministic library-path construction."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from string import Formatter

DEFAULT_LAYOUT = "{studio}/{year}/{title}/{quality}/{title}"
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WHITESPACE = re.compile(r"\s+")
_TOKENS = frozenset({"studio", "title", "year", "quality"})


def sanitize_component(value: str) -> str:
    result = _WHITESPACE.sub(" ", _INVALID.sub(" ", value)).strip(" .")
    return result if result and result not in {".", ".."} else "unknown"


def build_library_path(
    studio: str | None,
    title: str,
    release_date: str | None,
    suffix: str,
    layout: str = DEFAULT_LAYOUT,
    quality: str | None = None,
) -> PurePosixPath:
    """Build a safe library-relative filename from a token layout.

    The layout represents the complete relative path without the source suffix.
    Its default keeps files below ``studio/year/title/quality`` as documented.
    """
    values = {
        "studio": sanitize_component(studio or "unknown"),
        "title": sanitize_component(title),
        "year": sanitize_component((release_date or "unknown")[:4]),
        "quality": sanitize_component(quality or "unknown"),
    }
    _validate_layout(layout)
    directory = layout.format(**values)
    parts = [
        sanitize_component(part)
        for part in PurePosixPath(directory).parts
        if part not in {"/", ".", ".."}
    ]
    if not parts:
        raise ValueError("layout must contain a filename")
    return PurePosixPath(*parts[:-1], parts[-1] + suffix)


def _validate_layout(layout: str) -> None:
    if not layout or PurePosixPath(layout).is_absolute():
        raise ValueError("layout must be a non-empty relative path")
    for _, token, format_spec, conversion in Formatter().parse(layout):
        if token is not None and (token not in _TOKENS or format_spec or conversion):
            raise ValueError("layout contains an unsupported token")
