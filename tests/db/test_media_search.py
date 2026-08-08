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
    assert "ORDER BY media_files.size DESC" in rendered
