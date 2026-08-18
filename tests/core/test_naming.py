from __future__ import annotations

import pytest

from pornarr_core.naming import ReleaseName, normalize_title


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


def test_a_scene_release_name_splits_into_a_studio_and_a_readable_title() -> None:
    from pornarr_core.naming import split_release_name

    assert split_release_name("Brazzers - Late Night Shift (2024-05-01) 2160p WEB-DL x265-GRP") == (
        ReleaseName(studio="Brazzers", title="Late Night Shift")
    )


def test_a_name_without_a_studio_keeps_its_whole_title() -> None:
    from pornarr_core.naming import split_release_name

    assert split_release_name("some.scene.title.2023.1080p.web-dl.x264-abc") == ReleaseName(
        studio=None, title="some scene title"
    )


def test_a_name_that_is_only_tokens_still_gets_called_something() -> None:
    from pornarr_core.naming import split_release_name

    assert split_release_name("1080p WEB-DL").title == "1080p WEB-DL"
