"""The arithmetic behind the filter sidebar.

Pure counting, tested without a database, because the interesting claims are all
about which selections apply to which dimension — the part that is wrong in most
facet implementations and invisible until someone clicks a number and gets
fewer rows than it promised.
"""

from __future__ import annotations

from pornarr_api.facets import Counted, duration_band, tally


def _row(
    *,
    studio: str | None = None,
    resolution: str | None = None,
    duration: float | None = None,
    rating: float | None = None,
    tags: tuple[str, ...] = (),
) -> Counted:
    return Counted(
        studio=studio,
        resolution=resolution,
        duration_seconds=duration,
        rating=rating,
        tags=frozenset(tags),
    )


def _tally(rows: list[Counted], **selections: object):
    defaults: dict[str, object] = {
        "selected_studio": None,
        "selected_resolution": None,
        "selected_duration": None,
        "selected_rating": None,
        "selected_tag": None,
        "capped": False,
    }
    defaults.update(selections)
    return tally(rows, **defaults)  # type: ignore[arg-type]


def counts(values) -> dict[str, int]:
    return {item.value: item.count for item in values}


def test_a_dimension_does_not_narrow_itself() -> None:
    """Selecting 2160p must still show what 1080p would give.

    A facet list that collapses to the one thing you already chose is a dead
    end: there is no way back to the alternative without clearing the filter
    and losing your place.
    """
    rows = [
        _row(resolution="2160p", studio="Northwind"),
        _row(resolution="1080p", studio="Northwind"),
        _row(resolution="1080p", studio="Halcyon"),
    ]

    facets = _tally(rows, selected_resolution="2160p")

    assert counts(facets.resolution) == {"1080p": 2, "2160p": 1}
    # And the other dimensions do narrow, because 2160p applies to them.
    assert counts(facets.studio) == {"Northwind": 1}


def test_matched_is_what_the_list_will_show() -> None:
    rows = [
        _row(resolution="2160p", studio="Northwind"),
        _row(resolution="1080p", studio="Northwind"),
        _row(resolution="1080p", studio="Halcyon"),
    ]

    assert _tally(rows).matched == 3
    assert _tally(rows, selected_resolution="1080p").matched == 2
    assert _tally(rows, selected_resolution="1080p", selected_studio="Halcyon").matched == 1


def test_rating_floors_are_cumulative() -> None:
    """A floor includes everything above it, or the floors read as buckets."""
    rows = [_row(rating=5.0), _row(rating=4.0), _row(rating=3.0), _row(rating=None)]

    facets = _tally(rows)

    assert counts(facets.rating) == {"5": 1, "4": 2, "3": 3, "unrated": 1}


def test_an_unrated_title_is_counted_as_unrated_not_as_zero() -> None:
    rows = [_row(rating=None), _row(rating=None), _row(rating=5.0)]

    facets = _tally(rows)

    # Nothing rated it. That is a different statement from "rated badly", and
    # the design lists it as its own row for exactly that reason.
    assert counts(facets.rating)["unrated"] == 2
    assert _tally(rows, selected_rating=3).matched == 1


def test_a_title_with_no_studio_is_absent_rather_than_counted_as_blank() -> None:
    rows = [_row(studio="Northwind"), _row(studio=None)]

    facets = _tally(rows)

    assert counts(facets.studio) == {"Northwind": 1}


def test_tags_count_once_per_title_not_once_per_pairing() -> None:
    rows = [_row(tags=("night", "slow")), _row(tags=("night",))]

    facets = _tally(rows)

    assert counts(facets.tag) == {"night": 2, "slow": 1}


def test_duration_bands_are_half_open_so_a_boundary_lands_in_one_of_them() -> None:
    # 600 exactly is the start of the 10-to-20 band, not the end of under-10. Closed on both
    # sides and a 10-minute clip would be counted twice.
    assert duration_band(599) == "under_10"
    assert duration_band(600) == "10_20"
    assert duration_band(1_200) == "20_40"
    assert duration_band(2_400) == "over_40"
    assert duration_band(None) is None


def test_duration_facets_keep_the_designed_order_not_the_common_first_order() -> None:
    rows = [
        _row(duration=300),
        _row(duration=1_500),
        _row(duration=1_600),
        _row(duration=1_700),
    ]

    facets = _tally(rows)

    # Shortest to longest. A duration list sorted by popularity reads as noise.
    assert [item.value for item in facets.duration] == ["under_10", "20_40"]


def test_the_ordering_is_stable_when_counts_tie() -> None:
    rows = [_row(studio="Zephyr"), _row(studio="Aurora")]

    facets = _tally(rows)

    # Alphabetical within a tie, so the sidebar does not reshuffle between
    # identical requests.
    assert [item.value for item in facets.studio] == ["Aurora", "Zephyr"]


def test_a_selection_that_matches_nothing_reports_zero_rather_than_everything() -> None:
    rows = [_row(studio="Northwind"), _row(studio="Halcyon")]

    facets = _tally(rows, selected_studio="Meridian")

    assert facets.matched == 0
    # The other dimensions are empty too — there is nothing left to filter.
    assert facets.resolution == []
