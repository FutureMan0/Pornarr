from __future__ import annotations

import pytest

from pornarr_core.eta import (
    Confidence,
    Job,
    Protocol,
    queued_estimate,
    running_estimate,
    search_estimate,
    total_estimate,
)


def test_running_job_prefers_the_client_estimate_as_a_range() -> None:
    estimate = running_estimate(
        client_seconds=100, remaining_bytes=10_000, average_bytes_per_second=1
    )

    assert estimate.low_seconds == 80
    assert estimate.high_seconds == 120
    assert estimate.confidence is Confidence.HIGH


def test_running_job_falls_back_to_its_moving_average() -> None:
    estimate = running_estimate(
        client_seconds=None, remaining_bytes=6_000, average_bytes_per_second=100
    )

    assert (estimate.low_seconds, estimate.high_seconds, estimate.confidence) == (
        48,
        72,
        Confidence.MEDIUM,
    )


@pytest.mark.parametrize("seeders", [None, 0])
def test_torrent_without_seeders_is_unknown(seeders: int | None) -> None:
    assert search_estimate(Protocol.TORRENT, 1_000, 100, seeders=seeders).is_unknown


def test_protocol_aware_search_estimates_return_ranges() -> None:
    usenet = search_estimate(Protocol.USENET, 8_000, 100, seeders=None)
    torrent = search_estimate(Protocol.TORRENT, 8_000, 100, seeders=4)

    assert (usenet.low_seconds, usenet.high_seconds) == (64, 96)
    assert (torrent.low_seconds, torrent.high_seconds) == (160, 240)


def test_higher_priority_job_adds_queue_time_to_jobs_behind_it() -> None:
    waiting = [Job(priority=50, remaining_seconds=100), Job(priority=40, remaining_seconds=200)]

    assert queued_estimate(priority=40, waiting=waiting).low_seconds == 240
    assert queued_estimate(priority=60, waiting=waiting).low_seconds == 0


def test_manual_priority_preempts_automatic_work_in_a_mixed_queue() -> None:
    waiting = [
        Job(priority=90, remaining_seconds=30),
        Job(priority=80, remaining_seconds=60),
        Job(priority=60, remaining_seconds=120),
        Job(priority=40, remaining_seconds=240),
    ]

    assert queued_estimate(priority=100, waiting=waiting).low_seconds == 0
    assert queued_estimate(priority=80, waiting=waiting).low_seconds == 72
    assert queued_estimate(priority=60, waiting=waiting).low_seconds == 168
    assert queued_estimate(priority=40, waiting=waiting).low_seconds == 360


def test_total_estimate_adds_measured_unpack_and_import_ranges() -> None:
    result = total_estimate(
        search_estimate(Protocol.USENET, 8_000, 100, seeders=None),
        unpack_seconds=20,
        import_seconds=10,
    )

    assert (result.low_seconds, result.high_seconds) == (94, 126)


def test_unknown_transfer_stays_unknown_after_post_processing() -> None:
    result = total_estimate(
        search_estimate(Protocol.TORRENT, 8_000, 100, seeders=0),
        unpack_seconds=20,
        import_seconds=10,
    )

    assert result.is_unknown


def test_running_job_without_a_client_or_moving_average_is_unknown() -> None:
    assert running_estimate(
        client_seconds=None, remaining_bytes=8_000, average_bytes_per_second=0
    ).is_unknown


@pytest.mark.parametrize(
    ("seeders", "low_seconds", "high_seconds"),
    [
        # ADR 0002: the seeder factor is the whole of the difference. Ten seeders
        # or more and a torrent is estimated exactly as usenet is; below that it
        # is scaled down in proportion, and at zero there is no estimate at all.
        (1, 640, 960),
        (2, 320, 480),
        (5, 128, 192),
        (9, 71, 106),
        (10, 64, 96),
        (100, 64, 96),
    ],
)
def test_the_seeder_factor_is_what_makes_a_torrent_estimate_differ_from_usenet(
    seeders: int, low_seconds: int, high_seconds: int
) -> None:
    usenet = search_estimate(Protocol.USENET, 8_000, 100, seeders=None)
    torrent = search_estimate(Protocol.TORRENT, 8_000, 100, seeders=seeders)

    assert (usenet.low_seconds, usenet.high_seconds) == (64, 96)
    assert (torrent.low_seconds, torrent.high_seconds) == (low_seconds, high_seconds)
    assert torrent.confidence is usenet.confidence is Confidence.LOW


def test_a_usenet_release_with_no_seeders_is_still_estimated() -> None:
    """The negative space of the seeder rule: it must not fire on usenet.

    A usenet release has no seeders by definition, so treating "no seeders" as
    "no estimate" for both protocols would silently remove every usenet
    estimate there is.
    """
    assert not search_estimate(Protocol.USENET, 8_000, 100, seeders=None).is_unknown
    assert not search_estimate(Protocol.USENET, 8_000, 100, seeders=0).is_unknown
    assert search_estimate(Protocol.TORRENT, 8_000, 100, seeders=0).is_unknown


def test_queue_position_is_the_one_thing_that_does_not_differ_by_protocol() -> None:
    """ADR 0002 says queue semantics differ between the protocols. They do not.

    `queued_estimate` takes a priority and a list of waiting jobs and nothing
    else - there is no protocol argument and no per-protocol lane, so a usenet
    job and a torrent job of the same priority wait behind each other exactly
    as they would behind their own kind. Recorded here rather than asserted as
    correct: see .gauntlet/pieces/04-acquisition/HOLES.md 04.6.
    """
    mixed = [Job(priority=80, remaining_seconds=100), Job(priority=80, remaining_seconds=200)]

    assert queued_estimate(priority=80, waiting=mixed).low_seconds == 240
