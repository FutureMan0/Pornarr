"""The numbers beside the search filters.

A facet count is a promise: "select this and you will see 412 titles". The one
thing it must never do is disagree with the list underneath it, and that is
harder than it looks here, because a local search is not one SQL statement. It
is a statement, then a visibility scope, then a rating filter, then a per-user
content filter that can reject a row for reasons no query expresses.

So the counts are computed from the same pipeline as the results rather than
from a parallel aggregate query. Counting in SQL would be faster and would be
wrong in exactly the cases that matter — a household with content rules would
see numbers it can never reach.

THE COST, STATED. This walks the whole matching set rather than one page, so it
is bounded by `FACET_CAP`. A search matching more than that reports
`capped: true` and the interface says so; a silently truncated count is the same
lie in a quieter voice.

EACH FACET EXCLUDES ITSELF. Selecting "2160p" must not collapse the resolution
list to a single row — you still need to see what 1080p would give. So the
counts for a dimension are computed with that dimension's own selection lifted,
which is what makes a facet sidebar navigable rather than a dead end.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from pornarr_db.media_search import MediaSearch, MediaSearchResult

# Enough for a household library several times over. Past this the honest
# answer is "more than this", not a number that took a second to produce.
FACET_CAP = 5_000

# The design's duration bands, in seconds. Open-ended at the top.
DURATION_BANDS: tuple[tuple[str, float, float | None], ...] = (
    ("under_10", 0, 600),
    ("10_20", 600, 1_200),
    ("20_40", 1_200, 2_400),
    ("over_40", 2_400, None),
)

# Rating floors, plus the unrated bucket the design lists alongside them.
RATING_FLOORS: tuple[int, ...] = (5, 4, 3)


@dataclass(frozen=True, slots=True)
class FacetValue:
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class Facets:
    studio: list[FacetValue]
    resolution: list[FacetValue]
    duration: list[FacetValue]
    rating: list[FacetValue]
    tag: list[FacetValue]
    matched: int
    capped: bool


def duration_band(seconds: float | None) -> str | None:
    """Which band a duration falls in, or None when the file never said."""
    if seconds is None:
        return None
    for name, low, high in DURATION_BANDS:
        if seconds >= low and (high is None or seconds < high):
            return name
    return None


@dataclass(frozen=True, slots=True)
class Counted:
    """One title, reduced to the fields the facets count."""

    studio: str | None
    resolution: str | None
    duration_seconds: float | None
    rating: float | None
    tags: frozenset[str]


def tally(
    rows: list[Counted],
    *,
    selected_studio: str | None,
    selected_resolution: str | None,
    selected_duration: str | None,
    selected_rating: int | None,
    selected_tag: str | None,
    capped: bool,
) -> Facets:
    """Count each dimension with its own selection lifted.

    `rows` must already have every *other* filter applied — this function does
    the lifting, not the filtering, so the caller passes the widest set and each
    dimension narrows it back down by the selections that are not its own.
    """

    def matches(
        row: Counted,
        *,
        ignore: str,
    ) -> bool:
        if ignore != "studio" and selected_studio is not None and row.studio != selected_studio:
            return False
        if (
            ignore != "resolution"
            and selected_resolution is not None
            and row.resolution != selected_resolution
        ):
            return False
        if (
            ignore != "duration"
            and selected_duration is not None
            and duration_band(row.duration_seconds) != selected_duration
        ):
            return False
        if (
            ignore != "rating"
            and selected_rating is not None
            and (row.rating is None or row.rating < selected_rating)
        ):
            return False
        return not (ignore != "tag" and selected_tag is not None and selected_tag not in row.tags)

    studios: Counter[str] = Counter()
    resolutions: Counter[str] = Counter()
    durations: Counter[str] = Counter()
    ratings: Counter[str] = Counter()
    tags: Counter[str] = Counter()

    for row in rows:
        if row.studio is not None and matches(row, ignore="studio"):
            studios[row.studio] += 1
        if row.resolution is not None and matches(row, ignore="resolution"):
            resolutions[row.resolution] += 1
        if matches(row, ignore="duration"):
            band = duration_band(row.duration_seconds)
            if band is not None:
                durations[band] += 1
        if matches(row, ignore="rating"):
            # Cumulative: "4 stars and up" includes the fives. Anything else
            # would make the floors read like exact buckets, which they are not.
            for floor in RATING_FLOORS:
                if row.rating is not None and row.rating >= floor:
                    ratings[str(floor)] += 1
            if row.rating is None:
                ratings["unrated"] += 1
        if matches(row, ignore="tag"):
            for tag in row.tags:
                tags[tag] += 1

    matched = sum(1 for row in rows if matches(row, ignore=""))

    return Facets(
        studio=_ordered(studios),
        resolution=_ordered(resolutions),
        duration=[
            FacetValue(name, durations[name]) for name, _, _ in DURATION_BANDS if durations[name]
        ],
        rating=_rating_order(ratings),
        tag=_ordered(tags),
        matched=matched,
        capped=capped,
    )


def _ordered(counts: Counter[str]) -> list[FacetValue]:
    """Commonest first, then alphabetical so the order is stable between calls."""
    return [
        FacetValue(value, count)
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _rating_order(counts: Counter[str]) -> list[FacetValue]:
    """Highest floor first, with the unrated bucket last, as the design lists them."""
    ordered = [
        FacetValue(str(floor), counts[str(floor)]) for floor in RATING_FLOORS if counts[str(floor)]
    ]
    if counts["unrated"]:
        ordered.append(FacetValue("unrated", counts["unrated"]))
    return ordered


def widened(search: MediaSearch) -> MediaSearch:
    """The same search with every faceted dimension lifted.

    One trip to the database produces the set every facet is counted from; the
    narrowing then happens in `tally`, where it can be done once per dimension.
    """
    return replace(
        search,
        quality=None,
        studio=None,
        tag=None,
        minimum_duration_seconds=None,
        maximum_duration_seconds=None,
        cursor=None,
        limit=FACET_CAP + 1,
    )


def was_capped(results: list[MediaSearchResult]) -> bool:
    return len(results) > FACET_CAP
