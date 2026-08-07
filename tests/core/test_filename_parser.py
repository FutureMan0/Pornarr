from __future__ import annotations

from pathlib import PurePath

import pytest

from pornarr_core.filename_parser import FILENAME_PATTERNS, FILENAME_CONFIDENCE, parse_filename


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            PurePath("Studio/2024-01-02 - Alice Example - Performer One, Performer Two.mp4"),
            ("Studio", "Alice Example", "2024-01-02", ("Performer One", "Performer Two")),
        ),
        (
            PurePath("Studio/Alice Example (2024-01-02) [Performer One].mkv"),
            ("Studio", "Alice Example", "2024-01-02", ("Performer One",)),
        ),
    ],
)
def test_parses_common_filename_conventions(
    path: PurePath, expected: tuple[str, str, str, tuple[str, ...]]
) -> None:
    result = parse_filename(path)

    assert (result.studio, result.title, result.date, result.performers) == expected
    assert result.confidence == FILENAME_CONFIDENCE


def test_nfo_values_fill_filename_gaps() -> None:
    result = parse_filename(
        PurePath("downloads/unknown-file.mp4"),
        nfo_text="TITLE: Example\nSTUDIO: Studio\nDATE: 2024-01-02\nPERFORMERS: Alice, Bob",
    )

    assert result.title == "Example"
    assert result.studio == "Studio"
    assert result.performers == ("Alice", "Bob")
    assert result.confidence == FILENAME_CONFIDENCE


def test_unparseable_name_is_low_confidence_instead_of_an_error() -> None:
    result = parse_filename(PurePath("downloads/unparseable.mp4"))

    assert result.title == "unparseable"
    assert result.confidence == FILENAME_CONFIDENCE


def test_patterns_are_data_not_algorithm() -> None:
    assert FILENAME_PATTERNS
