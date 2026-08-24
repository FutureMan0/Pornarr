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


# --- Normalisation to a canonical title (docs/pipelines/import.md step 6) -----
#
# "Release group, resolution tokens, codec names, separators and date variants
# are stripped to a canonical title." Three tables: what must be stripped, what
# must survive untouched, and - kept rather than deleted, because it is the
# table that caught the over-stripping - the inputs where a token sits next to
# a real word.


@pytest.mark.parametrize(
    ("family", "release", "expected"),
    [
        ("resolution", "Alice Example 2160p", "alice example"),
        ("resolution", "Alice Example 1080p", "alice example"),
        ("resolution", "Alice Example 720p", "alice example"),
        ("resolution", "Alice Example 576p", "alice example"),
        ("resolution", "Alice Example 480p", "alice example"),
        ("source", "Alice Example WEBRip", "alice example"),
        ("source", "Alice Example BluRay", "alice example"),
        ("source", "Alice Example BDRip", "alice example"),
        ("source", "Alice Example DVDRip", "alice example"),
        ("source", "Alice Example HDTV", "alice example"),
        ("codec", "Alice Example x264", "alice example"),
        ("codec", "Alice Example x265", "alice example"),
        ("codec", "Alice Example h265", "alice example"),
        ("codec", "Alice Example HEVC", "alice example"),
        ("codec", "Alice Example AV1", "alice example"),
        ("release group", "Alice.Example-GROUP", "alice example"),
        ("release group", "Alice.Example-RARBG", "alice example"),
        ("release group", "Alice_Example_XYZ", "alice example"),
        ("separator", "Alice.Example", "alice example"),
        ("separator", "Alice-Example", "alice example"),
        ("separator", "Alice_Example", "alice example"),
        ("separator", "Alice/Example", "alice example"),
        ("separator", "Alice\\Example", "alice example"),
        ("separator", "Alice [Example]", "alice example"),
        ("separator", "Alice (Example)", "alice example"),
        ("separator", "Alice {Example}", "alice example"),
        ("date variant", "Alice Example 2024-01-02", "alice example 2024"),
        ("date variant", "Alice Example 2024.01.02", "alice example 2024"),
        ("date variant", "Alice Example 2024 01 02", "alice example 2024"),
        ("date variant", "Alice Example 2024_01_02", "alice example 2024"),
        ("date variant", "Alice Example 2024", "alice example 2024"),
        (
            "everything at once",
            "Alice.Example.2024.01.02.1080p.WEBRip.x264-GROUP",
            "alice example 2024",
        ),
        # The same families around a non-Latin title, which is where Jellyfin's
        # `CleanStringTests.cs` puts its hardest rows: the tokens have to go and
        # the title itself has to survive intact.
        ("non-Latin, everything at once", "아가씨 (2016) 1080p WEB-DL x264-GRP", "아가씨 2016"),
        ("non-Latin, everything at once", "千与千寻 2001 2160p HEVC", "千与千寻 2001"),
        (
            "non-Latin, everything at once",
            "Москва.2024.1080p.WEBRip.x264-GROUP",
            "москва 2024",
        ),
        ("accents survive the casefold", "Ñandú Café 720p", "ñandú café"),
    ],
)
def test_normalisation_strips_each_documented_token_family(
    family: str, release: str, expected: str
) -> None:
    assert normalize_title(release) == expected, family


@pytest.mark.parametrize(
    "title",
    [
        "Alice Example",
        # A number that is not a resolution token, next to a word that is not a
        # source name. The cleaner has to leave both alone.
        "The 1080 Project",
        "Blade Runner 2049",
        "Room 237",
        "The 400 Blows",
        "Catch 22",
        "Studio 54 Nights",
        "12 Angry Men",
        "Se7en",
        "Nineteen Eighty Four",
        # Non-Latin negative space, the way `CleanStringTests.cs` carries CJK and
        # Korean rows through Jellyfin's release-junk table. `normalize_title`
        # casefolds and then strips by pattern, and none of these patterns may
        # fire on a script whose words are not separated the way English is.
        "東京物語",
        "千と千尋の神隠し",
        "기생충",
        "아가씨",
        "москва слезам не верит",
        "ωμέγα",
        "ñandú café",
        "أفلام",
        "мосфильм 1965",
    ],
)
def test_normalisation_declines_to_fire_on_titles_that_only_look_like_releases(
    title: str,
) -> None:
    """The negative space: a normaliser tested only on inputs it changes is half tested."""
    assert normalize_title(title) == title.casefold()


@pytest.mark.parametrize(
    ("release", "wanted"),
    [
        # Was "alice example web" / "alice example h": the group strip ran before
        # the token pass and took the `DL` and the `264` for a release group.
        ("Alice Example WEB-DL", "alice example"),
        ("Alice Example H.264", "alice example"),
        # Was "club" / "country" / "blues" / "the diaries" / "alice street": a
        # token pattern substituted across the whole string removed a real word.
        ("AV1 Club", "av1 club"),
        ("DTS Country", "dts country"),
        ("AAC Blues", "aac blues"),
        ("The HEVC Diaries", "the hevc diaries"),
        ("Alice 480p Street", "alice 480p street"),
    ],
)
def test_normalisation_strips_no_more_than_the_document_asks_for(release: str, wanted: str) -> None:
    """Step 6 of `docs/pipelines/import.md`, read as a positional rule.

    Release tokens and the release group are removed where a release actually
    puts them - at the end, behind the title - and nowhere else. Both halves of
    the over-stripping this records were load bearing: the canonical title is
    what matching compares and what the library path is built from, so a title
    that lost a word to a codec name was misfiled and never matched again.
    """
    assert normalize_title(release) == wanted


@pytest.mark.parametrize(
    ("release", "studio", "title"),
    [
        (
            "Brazzers - Late Night Shift (2024-05-01) 2160p WEB-DL x265-GRP",
            "Brazzers",
            "Late Night Shift",
        ),
        ("Vixen - Golden Hour (2025-11-02) 1080p", "Vixen", "Golden Hour"),
        ("some.scene.title.2023.1080p.web-dl.x264-abc", None, "some scene title"),
        # A studio longer than `MAXIMUM_STUDIO_LENGTH` is not a studio; the
        # whole name stays the title, with the dash flattened like any other
        # separator.
        (f"{'S' * 65} - Title", None, f"{'S' * 65} Title"),
        # Several dashes: only the first segment can be the studio.
        ("Studio - Part One - Part Two 1080p", "Studio", "Part One Part Two"),
    ],
)
def test_a_release_name_splits_into_the_studio_and_the_title_a_reader_wants(
    release: str, studio: str | None, title: str
) -> None:
    from pornarr_core.naming import split_release_name

    assert split_release_name(release) == ReleaseName(studio=studio, title=title)
