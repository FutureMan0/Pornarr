from __future__ import annotations

import pytest

from pornarr_core.naming import normalize_title


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("Alice.Example.2024.1080p.WEB-DL.x264-GROUP", "alice example 2024"),
        ("Alice_Example_2024-01-02_2160p_HEVC", "alice example 2024"),
        ("Alice [BluRay] (2024)", "alice 2024"),
        ("Performer Name - A Meaningful Title - 720p", "performer name a meaningful title"),
    ],
)
def test_normalizes_realistic_release_names(release: str, expected: str) -> None:
    assert normalize_title(release) == expected


def test_normalization_is_idempotent() -> None:
    release = "Alice.Example.2024.1080p.WEB-DL.x264-GROUP"
    assert normalize_title(normalize_title(release)) == normalize_title(release)


def test_token_only_title_retains_a_comparable_value() -> None:
    assert normalize_title("1080p.WEB-DL.x264") == "1080p web dl x264"
