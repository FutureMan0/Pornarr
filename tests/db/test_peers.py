"""Paging across several libraries at once.

The merge is where a federated library either works or quietly lies, so these
tests page all the way to the end and assert on the whole sequence rather than
on one page: a merge that repeats or drops an item looks fine on page one.
"""

from __future__ import annotations

import pytest

from pornarr_db.peers import decode_cursor, encode_cursor, merge_sources


def _value(value: object) -> tuple[object, ...]:
    return (value,)


def _page(
    pages: dict[str, list[int]], offsets: dict[str, int], limit: int
) -> tuple[list[tuple[str, int]], dict[str, int], bool]:
    merged = merge_sources(
        {
            name: items[offsets.get(name, 0) : offsets.get(name, 0) + limit]
            for name, items in pages.items()
        },
        offsets,
        key=_value,
        descending=True,
        limit=limit,
    )
    return list(merged.items), merged.offsets, merged.exhausted


def test_a_cursor_survives_a_round_trip_and_refuses_another_sort() -> None:
    cursor = encode_cursor("added", {"local": 48, "peer": 12})

    assert decode_cursor(cursor, "added") == {"local": 48, "peer": 12}
    with pytest.raises(ValueError, match="different sort"):
        decode_cursor(cursor, "title")


@pytest.mark.parametrize("cursor", ["", "not-base64!", "aGVsbG8=", "{}"])
def test_a_cursor_the_client_invented_is_refused(cursor: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(cursor, "added")


def test_a_negative_offset_is_not_a_cursor() -> None:
    with pytest.raises(ValueError):
        decode_cursor(encode_cursor("added", {"local": -1}), "added")


def test_paging_two_libraries_shows_every_item_exactly_once() -> None:
    """The property that matters: the pages concatenated are the merged whole."""

    pages = {"local": [9, 7, 5, 3, 1], "peer": [10, 8, 6, 4, 2]}
    offsets: dict[str, int] = {}
    seen: list[tuple[str, int]] = []
    while True:
        items, offsets, _ = _page(pages, offsets, limit=3)
        if not items:
            break
        seen.extend(items)

    assert [value for _, value in seen] == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert [name for name, _ in seen[:4]] == ["peer", "local", "peer", "local"]
    assert len(seen) == len(set(seen))


def test_a_source_that_lost_every_comparison_is_asked_again_not_skipped() -> None:
    """Its offset must not move, or its items vanish between pages."""

    merged = merge_sources(
        {"local": [9, 8, 7], "peer": [1, 0]},
        {},
        key=_value,
        descending=True,
        limit=3,
    )

    assert [value for _, value in merged.items] == [9, 8, 7]
    assert merged.offsets == {"local": 3, "peer": 0}
    assert not merged.exhausted


def test_an_ascending_merge_reads_the_other_end_of_each_source() -> None:
    merged = merge_sources(
        {"local": ["alpha", "gamma"], "peer": ["beta", "delta"]},
        {},
        key=_value,
        descending=False,
        limit=3,
    )

    assert [value for _, value in merged.items] == ["alpha", "beta", "delta"]


def test_two_libraries_holding_the_same_title_keep_a_stable_order() -> None:
    """Registering yourself as your own peer must not shuffle from page to page."""

    sources = {"a-peer": [5, 4], "local": [5, 4]}
    first = merge_sources(sources, {}, key=_value, descending=True, limit=2)
    again = merge_sources(dict(reversed(sources.items())), {}, key=_value, descending=True, limit=2)

    assert first.items == again.items
    assert first.offsets == again.offsets
