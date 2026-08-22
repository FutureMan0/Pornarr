from __future__ import annotations

import pytest

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


# --- Placement path construction (docs/pipelines/import.md step 9) ------------
#
# "A hardlink into /data/library/{studio}/{year}/{normalized_title}/{quality}/
# {normalized_title}{extension}. This is the default token layout; every
# component is sanitised, and installations may reorder the studio, year, title
# and quality tokens."


@pytest.mark.parametrize(
    ("what", "component", "expected"),
    [
        ("less than", "a<b", "a b"),
        ("greater than", "a>b", "a b"),
        ("colon", "a:b", "a b"),
        ("double quote", 'a"b', "a b"),
        ("forward slash", "a/b", "a b"),
        ("backslash", "a\\b", "a b"),
        ("pipe", "a|b", "a b"),
        ("question mark", "a?b", "a b"),
        ("asterisk", "a*b", "a b"),
        ("null byte", "a\x00b", "a b"),
        ("unit separator", "a\x1fb", "a b"),
        ("tab", "a\tb", "a b"),
        ("newline", "a\nb", "a b"),
        ("a run of them", "a<<>>b", "a b"),
        ("collapsed whitespace", "a  b", "a b"),
        ("surrounding whitespace", "  spaced  ", "spaced"),
        ("a trailing dot, which Windows drops silently", "trailing.", "trailing"),
        ("a leading dot, which hides the directory", ".leading", "leading"),
        ("nothing left but dots", "...", "unknown"),
        ("the parent directory", "..", "unknown"),
        ("the current directory", ".", "unknown"),
        ("nothing at all", "", "unknown"),
        ("nothing but space", "   ", "unknown"),
        ("nothing but separators", "///", "unknown"),
        ("a traversal attempt", "../../etc/passwd", "etc passwd"),
        # Non-Latin components, because `_INVALID` passes every code point above
        # `\x1f` straight into a real filesystem path: the separator still has to
        # go, and the script around it still has to survive intact. Jellyfin's
        # naming tables carry the same CJK and Korean negative space.
        ("a separator inside Cyrillic", "Москва/Ленинград", "Москва Ленинград"),
        ("a colon inside Hangul", "기생충 : 감독판", "기생충 감독판"),
        ("an angle bracket beside Greek", "Ω<mega>", "Ω mega"),
        ("an ideographic space", "日本語　全角", "日本語 全角"),
    ],
)
def test_every_dangerous_component_is_sanitised(what: str, component: str, expected: str) -> None:
    assert sanitize_component(component) == expected, what


@pytest.mark.parametrize(
    "component",
    [
        "Rock & Roll",
        "Ann's Studio",
        "plus+plus",
        "50% Off",
        "#1 Hit",
        "a,b",
        "a;b",
        "a=b",
        "Bad Company (Reissue)",
        "Nine Inch Nails",
        "Studio 54",
        # A filesystem accepts these, so the sanitiser must not touch them.
        "千と千尋の神隠し",
        "기생충",
        "Ñandú Café",
        "Ⅷ",
        "Мосфильм",
    ],
)
def test_sanitising_declines_to_touch_what_a_filesystem_accepts(component: str) -> None:
    """The negative space: a title is not improved by having its punctuation removed."""
    assert sanitize_component(component) == component


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        (None, "Vixen/2024/Golden Hour/1080p/Golden Hour.mkv"),
        (
            "{studio}/{year}/{title}/{quality}/{title}",
            "Vixen/2024/Golden Hour/1080p/Golden Hour.mkv",
        ),
        ("{year}/{studio}/{quality}/{title}", "2024/Vixen/1080p/Golden Hour.mkv"),
        ("{quality}/{studio}/{year}/{title}", "1080p/Vixen/2024/Golden Hour.mkv"),
        ("{title}/{year}/{studio}/{quality}", "Golden Hour/2024/Vixen/1080p.mkv"),
        ("{studio}/{title}", "Vixen/Golden Hour.mkv"),
        ("{title}", "Golden Hour.mkv"),
        ("{studio} - {year}/{title}", "Vixen - 2024/Golden Hour.mkv"),
    ],
)
def test_an_installation_can_reorder_the_four_supported_tokens(
    layout: str | None, expected: str
) -> None:
    built = build_library_path(
        "Vixen",
        "Golden Hour",
        "2024-05-01",
        ".mkv",
        *(() if layout is None else (layout,)),
        quality="1080p",
    )

    assert built.as_posix() == expected


@pytest.mark.parametrize(
    ("what", "studio", "title", "release_date", "quality", "expected"),
    [
        (
            "a title that tries to climb out of the library",
            "Vixen",
            "../../etc/passwd",
            "2024-05-01",
            "1080p",
            "Vixen/2024/etc passwd/1080p/etc passwd.mkv",
        ),
        (
            "a studio that tries the same",
            "../..",
            "Golden Hour",
            "2024-05-01",
            "1080p",
            "unknown/2024/Golden Hour/1080p/Golden Hour.mkv",
        ),
        (
            "an unknown studio, date and quality",
            None,
            "Golden Hour",
            None,
            None,
            "unknown/unknown/Golden Hour/unknown/Golden Hour.mkv",
        ),
        (
            "the year is the first four characters of the date, not the whole date",
            "Vixen",
            "Golden Hour",
            "2024-05-01",
            None,
            "Vixen/2024/Golden Hour/unknown/Golden Hour.mkv",
        ),
    ],
)
def test_no_component_can_leave_the_library_root(
    what: str,
    studio: str | None,
    title: str,
    release_date: str | None,
    quality: str | None,
    expected: str,
) -> None:
    built = build_library_path(studio, title, release_date, ".mkv", quality=quality)

    assert built.as_posix() == expected, what
    assert ".." not in built.parts
    assert not built.is_absolute()


@pytest.mark.parametrize(
    "layout",
    ["/{studio}/{title}", "", "{filename}", "{title:>10}", "{title!r}", "{studio}/{unknown}"],
)
def test_an_unusable_layout_is_refused_rather_than_quietly_replaced(layout: str) -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        build_library_path("Vixen", "Golden Hour", "2024-05-01", ".mkv", layout)
