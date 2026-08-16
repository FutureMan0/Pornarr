"""Watch hotspots: the peak detection, and the trap it exists to avoid."""

from __future__ import annotations

import pytest

from pornarr_core.hotspots import (
    Hotspot,
    HotspotOptions,
    find_hotspots,
    has_enough_behaviour,
    local_baseline,
    plan_shorts,
    watch_histogram,
)

OPTIONS = HotspotOptions()


def _decaying(bins: int, start: int = 100, drop: int = 1) -> list[int]:
    """What every title looks like: everyone starts, some leave every moment."""
    return [max(0, start - drop * index) for index in range(bins)]


def test_options_reject_a_window_that_has_no_middle() -> None:
    with pytest.raises(ValueError, match="baseline window"):
        HotspotOptions(baseline_window_bins=4)
    with pytest.raises(ValueError, match="baseline window"):
        HotspotOptions(baseline_window_bins=1)
    with pytest.raises(ValueError, match="short length"):
        HotspotOptions(short_seconds=400, long_seconds=300)
    with pytest.raises(ValueError, match="bin size"):
        HotspotOptions(bin_seconds=0)


def test_positions_land_in_their_bin_and_out_of_range_samples_are_ignored() -> None:
    counts = watch_histogram([0.0, 9.9, 10.1, -5.0, 999.0], 60.0)

    assert counts[0] == 2
    assert counts[1] == 1
    assert sum(counts) == 3


def test_a_file_with_no_duration_has_no_histogram() -> None:
    assert watch_histogram([1.0], 0.0) == ()


def test_the_baseline_ignores_the_spike_sitting_in_its_own_window() -> None:
    counts = [10, 10, 10, 90, 10, 10, 10]

    assert local_baseline(counts, 5)[3] == 10.0


def test_a_wide_hot_passage_does_not_become_its_own_baseline() -> None:
    """A median would call a passage covering half its window normal."""

    counts = [1] * 5 + [100] * 11 + [1] * 5

    assert local_baseline(counts, 19)[10] == 1.0


def test_the_universal_start_of_file_bulge_is_not_a_hotspot() -> None:
    """The most-watched second of any video is the first one.

    Judged against the whole file, every title would get a short at 00:00.
    Judged against the local neighbourhood, plain decay produces nothing.
    """
    hotspots = find_hotspots(_decaying(120))

    assert hotspots == ()


def test_a_rewatched_moment_stands_out_from_the_decay_around_it() -> None:
    counts = _decaying(120)
    counts[60] += 400

    hotspots = find_hotspots(counts)

    assert len(hotspots) == 1
    assert hotspots[0].position_seconds == pytest.approx(600.0)


def test_hotspots_come_back_strongest_first() -> None:
    counts = _decaying(120)
    counts[30] += 150
    counts[80] += 400

    hotspots = find_hotspots(counts)

    assert [round(spot.position_seconds) for spot in hotspots] == [800, 300]


def test_flat_watching_produces_no_hotspot_rather_than_an_arbitrary_pick() -> None:
    assert find_hotspots([50] * 100) == ()


def test_an_empty_histogram_produces_nothing() -> None:
    assert find_hotspots([]) == ()


def test_a_narrow_spike_gets_a_minute_and_a_wide_one_gets_five() -> None:
    narrow = Hotspot(position_seconds=600.0, excess=400.0, run_bins=1)
    wide = Hotspot(position_seconds=1800.0, excess=380.0, run_bins=8)

    plans = plan_shorts([narrow, wide], 3600.0)

    assert [plan.duration_seconds for plan in plans] == [60.0, 300.0]


def test_a_short_starts_just_before_the_moment_people_jump_to() -> None:
    plans = plan_shorts([Hotspot(position_seconds=600.0, excess=400.0, run_bins=1)], 3600.0)

    assert plans[0].start_seconds == 595.0
    assert plans[0].end_seconds == 655.0


def test_a_scene_boundary_inside_the_lead_in_wins_the_start() -> None:
    """Opening on a cut beats opening halfway through a shot."""

    plans = plan_shorts(
        [Hotspot(position_seconds=600.0, excess=400.0, run_bins=1)],
        3600.0,
        snap_points_seconds=(412.0, 597.5, 900.0),
        options=OPTIONS,
    )

    # 595.0 without snapping; 597.5 is the scene cut inside the lead-in.
    assert plans[0].start_seconds == 597.5


def test_a_distant_scene_boundary_does_not_drag_the_start() -> None:
    plans = plan_shorts(
        [Hotspot(position_seconds=600.0, excess=400.0, run_bins=1)],
        3600.0,
        snap_points_seconds=(300.0, 900.0),
    )

    assert plans[0].start_seconds == 595.0


def test_a_hotspot_near_the_end_is_pulled_back_inside_the_file() -> None:
    plans = plan_shorts([Hotspot(position_seconds=1195.0, excess=400.0, run_bins=1)], 1200.0)

    assert plans[0].end_seconds == 1200.0
    assert plans[0].start_seconds == 1140.0


def test_a_clip_never_overlaps_one_that_already_exists() -> None:
    """Re-running on unchanged behaviour has to add nothing."""

    hotspot = Hotspot(position_seconds=600.0, excess=400.0, run_bins=1)

    assert plan_shorts([hotspot], 3600.0, existing_intervals=[(590.0, 660.0)]) == ()


def test_two_hotspots_too_close_together_yield_one_clip() -> None:
    plans = plan_shorts(
        [
            Hotspot(position_seconds=600.0, excess=400.0, run_bins=1),
            Hotspot(position_seconds=620.0, excess=390.0, run_bins=1),
        ],
        3600.0,
    )

    assert len(plans) == 1


def test_the_number_of_clips_per_title_is_capped() -> None:
    hotspots = [
        Hotspot(position_seconds=float(300 * index), excess=400.0 - index, run_bins=1)
        for index in range(1, 10)
    ]

    plans = plan_shorts(hotspots, 3600.0, options=HotspotOptions(maximum_per_title=2))

    assert len(plans) == 2


def test_a_title_shorter_than_a_clip_is_not_over_cut() -> None:
    plans = plan_shorts([Hotspot(position_seconds=20.0, excess=400.0, run_bins=1)], 30.0)

    assert plans[0].start_seconds == 0.0
    assert plans[0].end_seconds == 30.0


def test_one_person_rewatching_is_not_the_household_watching() -> None:
    assert has_enough_behaviour(400, 1) is False
    assert has_enough_behaviour(4, 4) is False
    assert has_enough_behaviour(40, 3) is True
