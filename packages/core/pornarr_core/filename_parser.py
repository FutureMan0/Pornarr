"""Conservative, I/O-free fallback metadata extraction from filenames and NFO text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

FILENAME_CONFIDENCE = 0.30
FILENAME_PATTERNS = (
    re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\s*-\s*(?P<title>.+?)\s*-\s*(?P<performers>.+)$"),
    re.compile(r"^(?P<title>.+?)\s*\((?P<date>\d{4}-\d{2}-\d{2})\)\s*\[(?P<performers>.+)\]$"),
)
_NFO_FIELDS = {
    "title": re.compile(r"^TITLE:\s*(.+)$", re.MULTILINE),
    "studio": re.compile(r"^STUDIO:\s*(.+)$", re.MULTILINE),
    "date": re.compile(r"^DATE:\s*(\d{4}-\d{2}-\d{2})$", re.MULTILINE),
    "performers": re.compile(r"^PERFORMERS?:\s*(.+)$", re.MULTILINE),
}


@dataclass(frozen=True, slots=True)
class FilenameMetadata:
    title: str
    studio: str | None
    date: str | None
    performers: tuple[str, ...]
    confidence: float = FILENAME_CONFIDENCE


def parse_filename(path: PurePath, *, nfo_text: str | None = None) -> FilenameMetadata:
    """Parse a fallback result without claiming more confidence than filenames warrant."""
    stem = path.stem.replace(".", " ").replace("_", " ")
    values = _from_pattern(stem)
    if nfo_text:
        values = _fill_nfo(values, nfo_text)
    if not values.get("studio") and (studio := _folder_studio(path)):
        values["studio"] = studio
    title = values.get("title") or stem
    return FilenameMetadata(
        title=title.strip(),
        studio=_optional(values.get("studio")),
        date=_optional(values.get("date")),
        performers=_performers(values.get("performers")),
    )


def _from_pattern(stem: str) -> dict[str, str]:
    for pattern in FILENAME_PATTERNS:
        if matched := pattern.match(stem):
            return {key: value.strip() for key, value in matched.groupdict().items() if value}
    return {}


def _folder_studio(path: PurePath) -> str | None:
    return path.parent.name or None


def _fill_nfo(values: dict[str, str], nfo_text: str) -> dict[str, str]:
    result = dict(values)
    for field, pattern in _NFO_FIELDS.items():
        if field not in result and (matched := pattern.search(nfo_text)):
            result[field] = matched.group(1).strip()
    return result


def _performers(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip()) if value else ()


def _optional(value: str | None) -> str | None:
    return value.strip() if value else None
