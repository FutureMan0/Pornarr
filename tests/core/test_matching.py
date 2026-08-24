"""Release-title parsing and explainable match scoring."""

from __future__ import annotations

from datetime import date

import pytest

from pornarr_core.matching import parse_release, score_release


@pytest.mark.parametrize(
    "title",
    [
        "Fake Studio - Compose Test Scene (2026) 1080p",
        "Studio - A Title Without A Date 2160p WEB-DL",
    ],
)
def test_an_undated_release_names_no_performers(title: str) -> None:
    """A title is not a person, and reading it as one poisons every later match."""

    assert parse_release(title).performers == ()


@pytest.mark.parametrize(
    ("title", "resolution", "source", "codec", "group", "release_date", "performers"),
    [
        (
            "Studio Name - Alice Example and Bob Star - 2024-05-03 - 2160p WEB-DL HEVC-GROUP",
            "2160p",
            "web-dl",
            "hevc",
            "GROUP",
            date(2024, 5, 3),
            ("Alice Example", "Bob Star"),
        ),
        (
            "Studio Name - Charlie Test - 2023.11.07.1080p.WEBRip.x264-Scene",
            "1080p",
            "webrip",
            "h264",
            "Scene",
            date(2023, 11, 7),
            ("Charlie Test",),
        ),
        (
            "Studio - Dana Sample - 2022_01_15 - 720p BluRay H.265-TEAM",
            "720p",
            "bluray",
            "hevc",
            "TEAM",
            date(2022, 1, 15),
            ("Dana Sample",),
        ),
        (
            "Studio - Erin Demo - 2021-06-09 - 480p HDTV AV1-RLS",
            "480p",
            "hdtv",
            "av1",
            "RLS",
            date(2021, 6, 9),
            ("Erin Demo",),
        ),
    ],
)
def test_parser_extracts_scene_attributes_from_realistic_titles(
    title: str,
    resolution: str,
    source: str,
    codec: str,
    group: str,
    release_date: date,
    performers: tuple[str, ...],
) -> None:
    parsed = parse_release(title)

    assert parsed.resolution == resolution
    assert parsed.source == source
    assert parsed.codec == codec
    assert parsed.group == group
    assert parsed.date == release_date
    assert parsed.performers == performers


def test_better_title_and_attribute_match_scores_higher_with_an_exact_breakdown() -> None:
    target = parse_release("Alice Example 2024-05-03 2160p WEB-DL HEVC")
    better = parse_release("Alice Example 2024-05-03 2160p WEB-DL HEVC-GROUP")
    worse = parse_release("Different Scene 720p CAM XVID-GROUP")

    better_score = score_release(target, better, indexer_reliability=0.9)
    worse_score = score_release(target, worse, indexer_reliability=0.4)

    assert better_score.total > worse_score.total
    assert better_score.total == sum(better_score.breakdown.values())
    assert better_score.breakdown["attributes"] > worse_score.breakdown["attributes"]


def test_invalid_date_and_reliability_bounds_do_not_distort_a_score() -> None:
    target = parse_release("Alice Example 2024-02-30 1080p WEB-DL H.264")
    candidate = parse_release("Alice Example 1080p WEB-DL x264")

    score = score_release(target, candidate, indexer_reliability=2)

    assert target.date is None
    assert score.breakdown["reliability"] == 0.1


@pytest.mark.parametrize(
    ("title", "site", "release_date"),
    [
        # The dominant convention on a Usenet indexer: site, a two-digit date,
        # then the scene. Every one of these is a real title shape returned by
        # a live Newznab search for category 6000.
        (
            "DesiBang.26.08.10.Amateur.Chubby.Woman.Gets.Nailed.XXX.1080p.MP4-WRB",
            "DesiBang",
            date(2026, 8, 10),
        ),
        ("Blacked.24.12.31.Emily.Willis.XXX.2160p.MP4-XXX", "Blacked", date(2024, 12, 31)),
        ("Tushy_Raw.25.01.05.Some.Scene.XXX.1080p.HEVC-GRP", "Tushy Raw", date(2025, 1, 5)),
        # Four-digit years keep working, and so does a site written with spaces.
        ("Fake Studio.2026.03.04.A Scene.1080p", "Fake Studio", date(2026, 3, 4)),
    ],
)
def test_a_scene_release_names_its_site_and_its_two_digit_date(
    title: str, site: str, release_date: date
) -> None:
    """ADR 0005 L9: the second cascade tier is site plus date plus title.

    Neither reached a provider before: `_DATE` only matched a four-digit year,
    which the scene convention never writes, and nothing ever set a site at
    all. Both tiers below the fingerprint were therefore dead, and every
    download that was not already hashed by the provider resolved at the
    filename tier - confidence 0.30, no studio, no performers, filed under
    `/unknown/unknown/`.
    """

    parsed = parse_release(title)
    assert parsed.site == site
    assert parsed.date == release_date


@pytest.mark.parametrize(
    "title",
    [
        "True.Amateurs.Solos.7.2026",
        "Studio - A Title Without A Date 2160p WEB-DL",
        "Hunting.4.Amateur.Pussy.7.XXX.DVDRip.x264-SUCKXXX",
    ],
)
def test_a_release_without_a_scene_date_names_no_site(title: str) -> None:
    """A site is only knowable where the convention puts one: before the date.

    Without a date the leading token is as likely to be the title's own first
    word, and guessing files an amateur pack under a studio that does not
    exist.
    """

    parsed = parse_release(title)
    assert parsed.site is None
    assert parsed.date is None


@pytest.mark.parametrize(
    "title",
    [
        # 32 is not a day and 13 is not a month, so neither is a date.
        "Studio.26.13.10.A.Scene.1080p",
        "Studio.26.08.32.A.Scene.1080p",
        # A resolution is not a date either.
        "Scene.Four.1080p.x264-GRP",
    ],
)
def test_a_number_that_is_not_a_date_is_not_read_as_one(title: str) -> None:
    parsed = parse_release(title)
    assert parsed.date is None
    assert parsed.site is None


def test_the_scene_title_drops_the_site_and_the_date_it_was_taken_from() -> None:
    """What is left is what a provider is asked about."""

    parsed = parse_release("DesiBang.26.08.10.Amateur.Chubby.Woman.Gets.Nailed.XXX.1080p.MP4-WRB")
    assert parsed.title == "Amateur Chubby Woman Gets Nailed"
