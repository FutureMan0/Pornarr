from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from pornarr_db.media_search import (
    TRIGRAM_THRESHOLD,
    MediaSearch,
    MediaSort,
    decode_cursor,
    encode_cursor,
    search_statement,
)


def test_cursor_round_trip_preserves_the_sort_key_and_media_id() -> None:
    media_id = uuid4()

    for sort_value in (date(2026, 8, 8), datetime(2026, 8, 8, 12, tzinfo=UTC)):
        cursor = encode_cursor((sort_value, media_id))

        assert decode_cursor(cursor) == (sort_value, media_id)


def test_relevance_search_uses_trigram_similarity_and_exact_match_boosting() -> None:
    statement = search_statement(MediaSearch(query="Summer Nites"))
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    assert "similarity" in rendered
    assert "normalized_title" in rendered
    assert "%%" in rendered
    assert "media.normalized_title <->" in rendered
    assert TRIGRAM_THRESHOLD == 0.2


def test_a_short_query_can_match_at_all() -> None:
    """Trigram similarity alone cannot answer two or three characters.

    `%` compares whole string to whole string, so a short query against a long
    title scores below the threshold however well it matches the beginning of it —
    "ni" found nothing while "night" found "Nightcall 04". A suggestion list that
    only answers complete words answers nothing anybody waits for, so a substring
    match is OR'd alongside.
    """
    rendered = str(search_statement(MediaSearch(query="ni")).compile(dialect=postgresql.dialect()))

    # Both clauses, joined: the fuzzy one still catches typos in a whole title.
    assert "media.normalized_title %%" in rendered
    assert "media.normalized_title LIKE" in rendered
    assert " OR " in rendered


def test_a_wildcard_in_the_query_is_not_a_wildcard() -> None:
    """Without escaping, one `%` in the box matches the entire library."""
    statement = search_statement(MediaSearch(query="100%"))
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    # SQLAlchemy's autoescape names an escape character rather than inlining the
    # pattern, which is what says the user's `%` is a literal.
    assert "ESCAPE" in rendered

    # The LIKE clauses bind an escaped copy of the text — `100/%`, where `/` is
    # SQLAlchemy's escape character — while the trigram operator binds the text as
    # typed, because `%` means nothing special to it. Both spellings present is
    # exactly the expected shape.
    bound = {
        value
        for value in statement.compile(dialect=postgresql.dialect()).params.values()
        if isinstance(value, str)
    }
    assert "100/%" in bound, "the LIKE patterns must treat the user's percent as a literal"
    assert "100%" in bound, "the trigram operator takes the text as typed"


def test_a_title_that_starts_with_the_query_scores_higher_than_one_that_merely_contains_it() -> (
    None
):
    rendered = str(
        search_statement(MediaSearch(query="night")).compile(dialect=postgresql.dialect())
    )

    # Three terms in the relevance expression: similarity, the exact-match bonus,
    # and the prefix bonus. The prefix bonus is the new one.
    assert rendered.count("normalized_title LIKE") >= 2
    assert "similarity" in rendered


def test_search_filters_and_sorting_are_composed_in_one_statement() -> None:
    statement = search_statement(
        MediaSearch(
            query="summer",
            quality="2160p",
            year=2025,
            studio="Northstar",
            performer="Alice",
            tag="outdoor",
            minimum_duration_seconds=300,
            maximum_duration_seconds=900,
            sort=MediaSort.SIZE,
            cursor=encode_cursor((1_000_000, uuid4())),
        )
    )
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    for table in ("media_files", "media_performers", "performers", "media_tags", "tags"):
        assert table in rendered
    assert "quality" in rendered
    assert "duration_seconds" in rendered
    assert "performers.normalized_name %%" in rendered
    # Not `%%`: a tag is chosen from the facet list by its exact name, and the
    # count beside it is a promise the rows have to keep. See `_media_has_tag`.
    assert "tags.normalized_name = " in rendered
    assert "ORDER BY media_files.size DESC" in rendered
