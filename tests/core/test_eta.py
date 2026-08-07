from __future__ import annotations

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


def test_torrent_with_zero_seeders_is_unknown() -> None:
    assert search_estimate(Protocol.TORRENT, 1_000, 100, seeders=0).is_unknown


def test_protocol_aware_search_estimates_return_ranges() -> None:
    usenet = search_estimate(Protocol.USENET, 8_000, 100, seeders=None)
    torrent = search_estimate(Protocol.TORRENT, 8_000, 100, seeders=4)

    assert (usenet.low_seconds, usenet.high_seconds) == (64, 96)
    assert (torrent.low_seconds, torrent.high_seconds) == (160, 240)


def test_higher_priority_job_adds_queue_time_to_jobs_behind_it() -> None:
    waiting = [Job(priority=50, remaining_seconds=100), Job(priority=40, remaining_seconds=200)]

    assert queued_estimate(priority=40, waiting=waiting).low_seconds == 240
    assert queued_estimate(priority=60, waiting=waiting).low_seconds == 0


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
