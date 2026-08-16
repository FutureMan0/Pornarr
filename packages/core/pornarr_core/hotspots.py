"""Find the parts of a title people actually watch, and cut shorts from them.

The signal is already there: every progress report writes a `progress` user
event carrying how far through the file the viewer was. Bucketed across
everyone on the server, that is a watch heatmap.

## The trap this has to avoid

The most-watched second of any video is the first one. Everybody starts at the
beginning and some fraction leaves at every moment after, so raw watch counts
decay from left to right on essentially every title. Picking "the most-watched
position" therefore produces a short at 00:00 for the entire library, which is
the one result that is both technically correct and completely useless.

So a bin is not judged against the whole file. It is judged against a *local
baseline* — the median of a wide window around it. A moment counts as a hotspot
when it stands out from its own neighbourhood, which is what a rewatched or
sought-to moment actually looks like. The universal front-loading lifts the
baseline along with the bins, and cancels out.

Nothing here is tuned to a fixed count. The threshold is derived from the
spread of the title's own excesses, so a heavily rewatched title yields several
shorts and an evenly watched one yields none rather than an arbitrary pick.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Ten seconds is the interval the player reports progress at, so a finer bin
# would invent resolution the data does not carry.
DEFAULT_BIN_SECONDS = 10.0
# A moment is compared against roughly three minutes around it: wide enough to
# span the natural decay, narrow enough that a real spike still stands out.
DEFAULT_BASELINE_WINDOW_BINS = 19
# See `local_baseline`: low enough that a wide hot passage cannot become its own
# baseline, high enough to ignore the odd empty bin.
DEFAULT_BASELINE_QUANTILE = 0.25
DEFAULT_SHORT_SECONDS = 60.0
DEFAULT_LONG_SECONDS = 300.0
# Start just before the moment people jump to, so a short opens on the run-up
# rather than dropping the viewer into the middle of it.
DEFAULT_LEAD_IN_SECONDS = 5.0
# Below this there is not enough behaviour to distinguish a hotspot from noise,
# and any peak found is one person's rewatch rather than the household's.
DEFAULT_MINIMUM_SAMPLES = 40
DEFAULT_MINIMUM_VIEWERS = 3
DEFAULT_MAXIMUM_PER_TITLE = 3
# A hot run this wide is a passage rather than a moment, and gets the long cut.
DEFAULT_LONG_RUN_BINS = 6
# A bin has to beat its neighbourhood by at least its own local level — a
# hundred percent more watching than the moments around it — and by an absolute
# number of samples, so a quiet title cannot produce a hotspot out of three
# extra views. The relative half is what adapts: on a heavily watched title the
# bar rises with the traffic instead of staying at a fixed count.
DEFAULT_RELATIVE_EXCESS = 1.0
DEFAULT_MINIMUM_EXCESS_SAMPLES = 5
# A flat floor of a handful of samples is not enough on its own: in the thin
# tail of a title, where the level has decayed to almost nothing, the slope
# across the baseline window is itself worth a few samples and clears it. The
# floor therefore also scales with how much the title was watched, which is the
# part that adapts — two percent of one title's traffic is a different number
# from two percent of another's.
DEFAULT_MINIMUM_EXCESS_FRACTION = 0.02


@dataclass(frozen=True, slots=True)
class HotspotOptions:
    bin_seconds: float = DEFAULT_BIN_SECONDS
    baseline_window_bins: int = DEFAULT_BASELINE_WINDOW_BINS
    baseline_quantile: float = DEFAULT_BASELINE_QUANTILE
    short_seconds: float = DEFAULT_SHORT_SECONDS
    long_seconds: float = DEFAULT_LONG_SECONDS
    lead_in_seconds: float = DEFAULT_LEAD_IN_SECONDS
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES
    minimum_viewers: int = DEFAULT_MINIMUM_VIEWERS
    maximum_per_title: int = DEFAULT_MAXIMUM_PER_TITLE
    long_run_bins: int = DEFAULT_LONG_RUN_BINS
    relative_excess: float = DEFAULT_RELATIVE_EXCESS
    minimum_excess_samples: int = DEFAULT_MINIMUM_EXCESS_SAMPLES
    minimum_excess_fraction: float = DEFAULT_MINIMUM_EXCESS_FRACTION

    def __post_init__(self) -> None:
        if self.bin_seconds <= 0:
            raise ValueError("bin size must be greater than zero")
        if self.baseline_window_bins < 3 or self.baseline_window_bins % 2 == 0:
            raise ValueError("baseline window must be an odd number of at least three bins")
        if not 0 <= self.baseline_quantile < 1:
            raise ValueError("baseline quantile must be at least zero and below one")
        if not 0 < self.short_seconds <= self.long_seconds:
            raise ValueError("short length must be positive and at most the long length")
        if self.lead_in_seconds < 0:
            raise ValueError("lead-in must not be negative")
        if self.minimum_samples < 1 or self.minimum_viewers < 1:
            raise ValueError("minimum sample and viewer counts must be at least one")
        if self.maximum_per_title < 1:
            raise ValueError("maximum per title must be at least one")
        if self.long_run_bins < 1:
            raise ValueError("long-run width must be at least one bin")
        if self.relative_excess <= 0:
            raise ValueError("relative excess must be greater than zero")
        if self.minimum_excess_samples < 1:
            raise ValueError("minimum excess samples must be at least one")
        if not 0 <= self.minimum_excess_fraction < 1:
            raise ValueError("minimum excess fraction must be at least zero and below one")


DEFAULT_OPTIONS = HotspotOptions()


@dataclass(frozen=True, slots=True)
class Hotspot:
    """A moment that stands out from the watching around it.

    Anchored at the start of the hot bin rather than its middle: the lead-in is
    supposed to put the clip *before* the moment, and half a bin of offset
    would swallow it.
    """

    position_seconds: float
    excess: float
    run_bins: int


@dataclass(frozen=True, slots=True)
class ShortPlan:
    """One short to cut, as an interval in seconds."""

    start_seconds: float
    end_seconds: float
    excess: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def watch_histogram(
    positions_seconds: Sequence[float],
    duration_seconds: float,
    *,
    options: HotspotOptions = DEFAULT_OPTIONS,
) -> tuple[int, ...]:
    """Count watch samples per time bin across the whole file."""
    if duration_seconds <= 0:
        return ()
    bins = max(1, int(duration_seconds // options.bin_seconds) + 1)
    counts = [0] * bins
    for position in positions_seconds:
        if position < 0 or position > duration_seconds:
            continue
        counts[min(bins - 1, int(position // options.bin_seconds))] += 1
    return tuple(counts)


def local_baseline(
    counts: Sequence[int], window_bins: int, quantile: float = DEFAULT_BASELINE_QUANTILE
) -> tuple[float, ...]:
    """A low quantile of the window around each bin, clamped at the edges.

    Not the median, and not the mean. The mean is dragged up by the very spike
    being looked for. The median survives a narrow spike but not a wide one: a
    hot passage covering more than half the window becomes the median, declares
    itself normal, and disappears. A quarter-point holds as long as the passage
    leaves a quarter of its neighbourhood cold, which a five-minute passage in a
    feature-length title always does.
    """
    if not counts:
        return ()
    half = window_bins // 2
    baseline = []
    for index in range(len(counts)):
        window = sorted(counts[max(0, index - half) : min(len(counts), index + half + 1)])
        baseline.append(float(window[min(len(window) - 1, int(len(window) * quantile))]))
    return tuple(baseline)


def find_hotspots(
    counts: Sequence[int],
    *,
    options: HotspotOptions = DEFAULT_OPTIONS,
) -> tuple[Hotspot, ...]:
    """Bins standing out from their own neighbourhood, strongest first."""
    if not counts:
        return ()
    baseline = local_baseline(counts, options.baseline_window_bins, options.baseline_quantile)
    excesses = [float(count) - base for count, base in zip(counts, baseline, strict=True)]
    # Per bin rather than one number for the file: the bar has to scale with
    # how busy that part of the title already is, or a spike early on where
    # everybody still is would always beat a spike late on where few people are.
    floor = max(
        float(options.minimum_excess_samples), sum(counts) * options.minimum_excess_fraction
    )
    thresholds = [max(floor, base * options.relative_excess) for base in baseline]

    hotspots: list[Hotspot] = []
    run_start: int | None = None
    for index, excess in [*enumerate(excesses), (len(excesses), float("-inf"))]:
        if index < len(excesses) and excess >= thresholds[index]:
            run_start = index if run_start is None else run_start
            continue
        if run_start is None:
            continue
        run = range(run_start, index)
        peak = max(run, key=lambda position: excesses[position])
        hotspots.append(
            Hotspot(
                position_seconds=peak * options.bin_seconds,
                excess=excesses[peak],
                run_bins=len(run),
            )
        )
        run_start = None
    return tuple(sorted(hotspots, key=lambda spot: (-spot.excess, spot.position_seconds)))


def plan_shorts(
    hotspots: Sequence[Hotspot],
    duration_seconds: float,
    *,
    existing_intervals: Sequence[tuple[float, float]] = (),
    snap_points_seconds: Sequence[float] = (),
    options: HotspotOptions = DEFAULT_OPTIONS,
) -> tuple[ShortPlan, ...]:
    """Turn hotspots into non-overlapping intervals to cut.

    A wide hot run is a passage rather than a moment and gets the long cut; a
    narrow spike gets the short one. Where a scene boundary sits inside the
    lead-in the start snaps to it, so the short opens on a cut instead of
    halfway through a shot.
    """
    if duration_seconds <= 0:
        return ()
    taken = [tuple(interval) for interval in existing_intervals]
    plans: list[ShortPlan] = []
    for hotspot in hotspots:
        if len(plans) >= options.maximum_per_title:
            break
        length = (
            options.long_seconds
            if hotspot.run_bins >= options.long_run_bins
            else options.short_seconds
        )
        length = min(length, duration_seconds)
        start = _snap(
            max(0.0, hotspot.position_seconds - options.lead_in_seconds),
            snap_points_seconds,
            options.lead_in_seconds,
        )
        start = min(start, duration_seconds - length)
        start = max(0.0, start)
        end = min(duration_seconds, start + length)
        if end - start <= 0 or any(
            start < other_end and other_start < end for other_start, other_end in taken
        ):
            continue
        taken.append((start, end))
        plans.append(ShortPlan(start_seconds=start, end_seconds=end, excess=hotspot.excess))
    return tuple(sorted(plans, key=lambda plan: plan.start_seconds))


def has_enough_behaviour(
    sample_count: int, viewer_count: int, *, options: HotspotOptions = DEFAULT_OPTIONS
) -> bool:
    """Whether a title has been watched enough for its peaks to mean anything.

    Both counts matter. One person rewatching a moment forty times is a
    personal habit, not a hotspot the household would recognise.
    """
    return sample_count >= options.minimum_samples and viewer_count >= options.minimum_viewers


def _snap(start: float, snap_points: Sequence[float], tolerance: float) -> float:
    candidates = [point for point in snap_points if abs(point - start) <= tolerance]
    return min(candidates, key=lambda point: abs(point - start)) if candidates else start
