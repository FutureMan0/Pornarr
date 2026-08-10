"""Release-title parsing and explainable match scoring."""

from __future__ import annotations

from datetime import date

import pytest

from pornarr_core.matching import parse_release, score_release


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
