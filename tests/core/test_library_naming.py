from __future__ import annotations

from pornarr_core.library_naming import build_library_path, sanitize_component


def test_library_path_uses_sanitized_default_studio_year_title_layout() -> None:
    assert (
        build_library_path("A/B: Studio", "Title? *", "2024-05-01", ".mkv").as_posix()
        == "A B Studio/2024/Title/unknown/Title.mkv"
    )


def test_an_undated_release_is_filed_under_a_readable_year() -> None:
    assert (
        build_library_path("Studio", "Title", None, ".mkv").as_posix()
        == "Studio/unknown/Title/unknown/Title.mkv"
    )


def test_custom_layout_can_reorder_supported_tokens() -> None:
    assert (
        build_library_path("Studio", "Title", None, ".mp4", "{title}/{studio}").as_posix()
        == "Title/Studio.mp4"
    )


def test_sanitize_component_never_returns_an_empty_or_traversing_path() -> None:
    assert sanitize_component("../") == "unknown"


def test_library_layout_rejects_absolute_paths_and_unknown_tokens() -> None:
    import pytest

    with pytest.raises(ValueError, match="relative"):
        build_library_path("Studio", "Title", None, ".mkv", "/{title}")
    with pytest.raises(ValueError, match="unsupported"):
        build_library_path("Studio", "Title", None, ".mkv", "{filename}")
