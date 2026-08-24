"""Migrations against a real PostgreSQL.

These run against a real database rather than SQLite or a mock, because the
things that break — extensions, server defaults, constraint naming, type
comparison — are exactly the things a substitute does not reproduce.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from json import dumps
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from pornarr_db.models.filters import FilterRuleKind

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("DATABASE_URL is not set. Integration tests need a real database.")
    return url


def _psycopg_url() -> str:
    return _database_url().replace("postgresql+psycopg://", "postgresql://")


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def clean_database() -> Iterator[None]:
    """Start and finish with an empty public schema."""
    _reset_schema()
    yield
    _reset_schema()


def _reset_schema() -> None:
    with psycopg.connect(_psycopg_url(), autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")


def test_upgrade_applies_from_an_empty_database(clean_database: None) -> None:
    result = _alembic("upgrade", "head")

    assert result.returncode == 0, result.stderr

    with psycopg.connect(_psycopg_url()) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert row is not None


def test_pg_trgm_is_available_after_upgrade(clean_database: None) -> None:
    """Trigram similarity carries local search, fuzzy matching and deduplication.
    Without the extension every one of those silently has no index to use."""
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        installed = connection.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
        ).fetchone()
        similarity = connection.execute("SELECT similarity('kitten', 'sitting')").fetchone()

    assert installed is not None
    assert similarity is not None
    assert 0.0 <= similarity[0] <= 1.0


def test_local_search_uses_its_trigram_index_within_200ms(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0
    media_rows = [
        (uuid4(), f"Other Scene {number}", f"other scene {number}") for number in range(10_000)
    ]
    file_rows = [
        (uuid4(), media_id, f"/library/{media_id}.mp4", 1_000_000) for media_id, _, _ in media_rows
    ]
    media_rows.append((uuid4(), "Summer Nites", "summer nites"))
    file_rows.append((uuid4(), media_rows[-1][0], "/library/summer-nites.mp4", 1_000_000))

    with psycopg.connect(_psycopg_url()) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO media (id, title, normalized_title) VALUES (%s, %s, %s)", media_rows
            )
            cursor.executemany(
                "INSERT INTO media_files (id, media_id, path, size) VALUES (%s, %s, %s, %s)",
                file_rows,
            )
        connection.execute("ANALYZE media")
        connection.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.2', true)")
        plan_row = connection.execute(
            """EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT media.id
            FROM media
            WHERE media.normalized_title % 'summer nites'
            ORDER BY media.normalized_title <-> 'summer nites', media.id
            LIMIT 50"""
        ).fetchone()

    assert plan_row is not None
    plan = plan_row[0][0]
    assert "ix_media_normalized_title_trgm_knn" in dumps(plan)
    assert plan["Execution Time"] < 200


def test_entity_name_trigram_indexes_tolerate_typos(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        performer_id = uuid4()
        tag_id = uuid4()
        connection.execute(
            "INSERT INTO performers (id, name, normalized_name, metadata) VALUES (%s, %s, %s, %s)",
            (performer_id, "Alice Example", "alice example", "{}"),
        )
        connection.execute(
            "INSERT INTO tags (id, name, normalized_name, metadata) VALUES (%s, %s, %s, %s)",
            (tag_id, "Outdoor", "outdoor", "{}"),
        )
        connection.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.2', true)")
        performer = connection.execute(
            "SELECT id FROM performers WHERE normalized_name %% %s", ("alise example",)
        ).fetchone()
        tag = connection.execute(
            "SELECT id FROM tags WHERE normalized_name %% %s", ("outdor",)
        ).fetchone()

    assert performer == (performer_id,)
    assert tag == (tag_id,)


def test_filter_migration_seeds_one_disabled_global_profile(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        profiles = connection.execute(
            "SELECT id, scope, user_id FROM content_filter_profiles"
        ).fetchall()
        rules = connection.execute(
            "SELECT kind, pattern, enabled FROM content_filter_rules"
        ).fetchall()

    assert len(profiles) == 1
    assert profiles[0][1:] == ("global", None)
    assert {(kind, pattern, enabled) for kind, pattern, enabled in rules} == {
        (kind.value, "", False) for kind in FilterRuleKind
    }


def test_quality_migration_seeds_one_usable_default_profile(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        profile = connection.execute(
            "SELECT id, minimum_custom_format_score FROM quality_profiles WHERE is_default"
        ).fetchone()
        definitions = connection.execute(
            "SELECT name FROM quality_definitions ORDER BY weight"
        ).fetchall()
        items = connection.execute(
            "SELECT position FROM quality_profile_items ORDER BY position"
        ).fetchall()

    assert profile is not None
    assert profile[1] == 0
    assert definitions == [("WEB 720p",), ("WEB 1080p",), ("WEB 2160p",)]
    assert items == [(0,), (1,), (2,)]


def test_deleting_a_user_removes_its_filter_profile(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        user_id = "c4ed1ec6-a2d4-4f2e-a7ce-33e1a0bc4b8a"
        profile_id = "d3f6332d-71cd-487f-b03f-0a1d2f6d9c31"
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "filter-owner", "not-a-real-password"),
        )
        connection.execute(
            "INSERT INTO content_filter_profiles (id, scope, user_id) VALUES (%s, %s, %s)",
            (profile_id, "user", user_id),
        )
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
        remaining = connection.execute(
            "SELECT count(*) FROM content_filter_profiles WHERE id = %s", (profile_id,)
        ).fetchone()
        connection.commit()

    assert remaining == (0,)


def test_deleting_a_monitored_performer_removes_its_monitor(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        user_id = str(uuid4())
        performer_id = str(uuid4())
        monitor_id = str(uuid4())
        profile = connection.execute("SELECT id FROM quality_profiles WHERE is_default").fetchone()
        assert profile is not None
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "monitor-owner", "not-a-real-password"),
        )
        connection.execute(
            "INSERT INTO performers (id, name, normalized_name, metadata) "
            "VALUES (%s, %s, %s, '{}'::jsonb)",
            (performer_id, "Example Performer", "example performer"),
        )
        connection.execute(
            "INSERT INTO monitors (id, user_id, kind, performer_id, quality_profile_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (monitor_id, user_id, "performer", performer_id, profile[0]),
        )
        connection.execute("DELETE FROM performers WHERE id = %s", (performer_id,))
        remaining = connection.execute(
            "SELECT count(*) FROM monitors WHERE id = %s", (monitor_id,)
        ).fetchone()
        connection.commit()

    assert remaining == (0,)


def test_indexer_rss_marker_is_available_after_upgrade(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        marker_column = connection.execute(
            """SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'indexers' AND column_name = 'last_rss_guid'"""
        ).fetchone()

    assert marker_column == ("last_rss_guid",)


def test_request_migration_flags_automatic_work_and_prioritizes_manual_requests(
    clean_database: None,
) -> None:
    assert _alembic("upgrade", "0036").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        user_id = str(uuid4())
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "request-owner", "not-a-real-password"),
        )
        connection.execute(
            "INSERT INTO requests (id, user_id, query, status, priority) VALUES (%s, %s, %s, %s, %s)",
            (str(uuid4()), user_id, "Manual request", "searching", 50),
        )
        connection.commit()

    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        request = connection.execute(
            "SELECT priority, is_automatic FROM requests WHERE user_id = %s", (user_id,)
        ).fetchone()

    assert request == (80, False)


def test_user_event_upgrade_preserves_completion_and_cascades_with_its_user(
    clean_database: None,
) -> None:
    assert _alembic("upgrade", "0030").returncode == 0
    user_id = "3ea5562a-3c8d-4ea2-8f2d-f0a6c6b11825"
    media_id = "db5f12e7-9835-47a8-a5b2-c8412432d5d0"
    event_id = "d1f20f59-9179-49e4-ae0b-c79f9d1a2faf"
    with psycopg.connect(_psycopg_url()) as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "event-owner", "not-a-real-password"),
        )
        connection.execute(
            "INSERT INTO media (id, title, normalized_title) VALUES (%s, %s, %s)",
            (media_id, "Example", "example"),
        )
        connection.execute(
            """INSERT INTO user_events (id, user_id, media_id, event_type)
            VALUES (%s, %s, %s, %s)""",
            (event_id, user_id, media_id, "playback.completed"),
        )
        connection.commit()

    result = _alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr

    with psycopg.connect(_psycopg_url()) as connection:
        migrated = connection.execute(
            "SELECT event_type, value, subject_id FROM user_events WHERE id = %s", (event_id,)
        ).fetchone()
        connection.execute(
            "INSERT INTO user_events (id, user_id, event_type) VALUES (%s, %s, %s)",
            ("838751e7-bae4-43cc-9796-bce1b36169fe", user_id, "search"),
        )
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'user_events'"
        ).fetchall()
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
        remaining = connection.execute("SELECT count(*) FROM user_events").fetchone()
        connection.commit()

    assert migrated == ("completed", None, None)
    assert "ix_user_events_user_id_created_at" in {name for (name,) in indexes}
    assert remaining == (0,)


def test_user_preferences_are_indexed_and_removed_with_their_user(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0
    user_id = "e20bc013-7619-45d9-8dfa-f1a6b75debd0"
    with psycopg.connect(_psycopg_url()) as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "preference-owner", "not-a-real-password"),
        )
        connection.execute(
            """INSERT INTO user_preferences (user_id, axis, subject, raw_score, score)
            VALUES (%s, %s, %s, %s, %s)""",
            (user_id, "tag", "example-tag", 8, 1),
        )
        connection.execute("INSERT INTO user_preference_states (user_id) VALUES (%s)", (user_id,))
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'user_preferences'"
        ).fetchall()
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
        remaining = connection.execute(
            """SELECT
                (SELECT count(*) FROM user_preferences),
                (SELECT count(*) FROM user_preference_states)"""
        ).fetchone()
        connection.commit()

    assert "ix_user_preferences_user_id_axis_score" in {name for (name,) in indexes}
    assert remaining == (0, 0)


def test_media_files_reject_two_active_rows_for_one_medium(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        media_id = "1f94c6e2-60a5-4e88-9682-06a6b9156a19"
        connection.execute(
            "INSERT INTO media (id, title, normalized_title) VALUES (%s, %s, %s)",
            (media_id, "Example", "example"),
        )
        connection.execute(
            "INSERT INTO media_files (id, media_id, path, size) VALUES (%s, %s, %s, %s)",
            ("588deaf0-1f8a-4721-bcae-9675dd10be85", media_id, "/library/one.mkv", 1),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "INSERT INTO media_files (id, media_id, path, size) VALUES (%s, %s, %s, %s)",
                ("3b192f41-6c60-4b04-8c42-b60c241a7a7a", media_id, "/library/two.mkv", 2),
            )


def test_deleting_media_cascades_to_files_and_history(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        media_id = "83b66c65-8224-489f-95a8-ae7a578a4f56"
        old_file_id = "7492c671-3c9f-48a4-af2a-044c2ec544ff"
        new_file_id = "e29e7871-515d-4698-a30a-5b88c7e7fe7f"
        connection.execute(
            "INSERT INTO media (id, title, normalized_title) VALUES (%s, %s, %s)",
            (media_id, "Example", "example"),
        )
        connection.execute(
            "INSERT INTO media_files (id, media_id, path, size, is_active) VALUES (%s, %s, %s, %s, %s)",
            (old_file_id, media_id, "/library/old.mkv", 1, False),
        )
        connection.execute(
            "INSERT INTO media_files (id, media_id, path, size) VALUES (%s, %s, %s, %s)",
            (new_file_id, media_id, "/library/new.mkv", 2),
        )
        connection.execute(
            "INSERT INTO media_file_history (id, replaced_file_id, replacement_file_id) VALUES (%s, %s, %s)",
            ("56d9ca22-77ba-4e14-b997-f7bb274bd877", old_file_id, new_file_id),
        )
        connection.execute("DELETE FROM media WHERE id = %s", (media_id,))
        remaining = connection.execute(
            "SELECT (SELECT count(*) FROM media_files), (SELECT count(*) FROM media_file_history)"
        ).fetchone()
        connection.commit()

    assert remaining == (0, 0)


def test_trigram_similarity_uses_the_media_title_index(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        connection.execute("SET enable_seqscan = off")
        plan = connection.execute(
            "EXPLAIN (COSTS OFF) SELECT id FROM media WHERE normalized_title % 'example'"
        ).fetchall()

    assert "ix_media_normalized_title_trgm" in "\n".join(row[0] for row in plan)


def test_download_queue_uses_the_status_priority_index(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        connection.execute("SET enable_seqscan = off")
        plan = connection.execute(
            "EXPLAIN (COSTS OFF) "
            "SELECT id FROM download_jobs WHERE status = 'queued' ORDER BY priority"
        ).fetchall()

    assert "ix_download_jobs_queue" in "\n".join(row[0] for row in plan)


def test_removing_download_client_and_job_preserves_history(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        client_id = "c342b6a0-c750-403d-8dbb-9c57483354a3"
        job_id = "2780a5c0-a99d-4b3c-952f-7252651ac801"
        history_id = "4bd4916e-466b-4e2d-b77b-3714c386e09d"
        connection.execute(
            "INSERT INTO download_clients "
            "(id, name, protocol, implementation, host, port, url_base, credentials, health) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                client_id,
                "Primary",
                "usenet",
                "sabnzbd",
                "sab.example",
                8080,
                "",
                "ciphertext",
                "healthy",
            ),
        )
        connection.execute(
            "INSERT INTO download_jobs "
            "(id, download_client_id, client_name, protocol, release_guid, client_job_id, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (job_id, client_id, "Primary", "usenet", "release-guid", "SABnzbd_nzo", "failed"),
        )
        connection.execute(
            "INSERT INTO download_history "
            "(id, download_job_id, client_name, client_job_id, protocol, release_guid, status, error) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                history_id,
                job_id,
                "Primary",
                "SABnzbd_nzo",
                "usenet",
                "release-guid",
                "failed",
                "download failed",
            ),
        )
        connection.execute("DELETE FROM download_clients WHERE id = %s", (client_id,))
        job = connection.execute(
            "SELECT download_client_id, client_name, release_guid FROM download_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        connection.execute("DELETE FROM download_jobs WHERE id = %s", (job_id,))
        history = connection.execute(
            "SELECT download_job_id, client_name, client_job_id, protocol, release_guid, status, error "
            "FROM download_history WHERE id = %s",
            (history_id,),
        ).fetchone()
        connection.commit()

    assert job == (None, "Primary", "release-guid")
    assert history == (
        None,
        "Primary",
        "SABnzbd_nzo",
        "usenet",
        "release-guid",
        "failed",
        "download failed",
    )


def test_performance_measurements_use_the_expected_metric_vocabulary(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        metrics = connection.execute(
            "SELECT unnest(enum_range(NULL::performance_metric))::text"
        ).fetchall()

    assert metrics == [
        ("download_speed",),
        ("post_processing_seconds_per_gib",),
        ("import_seconds_per_gib",),
        ("disk_write_speed",),
    ]


def test_automation_migration_backfills_disabled_rules(clean_database: None) -> None:
    assert _alembic("upgrade", "0017").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        user_id = "07262e37-b9e9-48b4-9a51-b1f81a0b0131"
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "automation-owner", "not-a-real-password"),
        )
        connection.commit()

    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        rule = connection.execute(
            """SELECT enabled, daily_download_limit_gb, max_concurrent_jobs, max_downloads_per_day
            FROM automation_rules WHERE user_id = %s""",
            (user_id,),
        ).fetchone()

    assert rule == (False, 10, 2, 3)


def test_request_survives_download_job_deletion(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        user_id = "8471c197-3db3-476f-9127-38d4d1ae5dcb"
        client_id = "d94b20e7-0502-456a-b6da-fba4e6bf0e4a"
        job_id = "ee337e00-eef6-4b1f-b7ca-9ce4d2a4b690"
        request_id = "de649d41-9adb-4f60-9ebc-8a4e6a9e4f75"
        connection.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)",
            (user_id, "request-owner", "not-a-real-password"),
        )
        connection.execute(
            "INSERT INTO download_clients (id, name, protocol, implementation, host, port, url_base, credentials, health) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                client_id,
                "client",
                "torrent",
                "qbittorrent",
                "client.example",
                8080,
                "",
                "ciphertext",
                "healthy",
            ),
        )
        connection.execute(
            "INSERT INTO download_jobs (id, download_client_id, client_name, protocol, release_guid, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (job_id, client_id, "client", "torrent", "release", "queued"),
        )
        connection.execute(
            "INSERT INTO requests (id, user_id, query, status, priority, download_job_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (request_id, user_id, "Example", "queued", 50, job_id),
        )
        connection.execute("DELETE FROM download_jobs WHERE id = %s", (job_id,))
        request = connection.execute(
            "SELECT status, download_job_id FROM requests WHERE id = %s", (request_id,)
        ).fetchone()
        connection.commit()

    assert request == ("queued", None)


def test_autogenerate_reports_no_drift(clean_database: None) -> None:
    """The models and the migration history must agree. When they do not, someone
    changed a model without writing a migration and the next deployment fails."""
    assert _alembic("upgrade", "head").returncode == 0

    result = _alembic("check")

    assert result.returncode == 0, f"schema drift detected:\n{result.stdout}\n{result.stderr}"


def test_downgrade_leaves_no_application_tables(clean_database: None) -> None:
    assert _alembic("upgrade", "head").returncode == 0
    assert _alembic("downgrade", "base").returncode == 0

    with psycopg.connect(_psycopg_url()) as connection:
        remaining = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()

    # alembic_version survives a downgrade to base; nothing of ours should.
    assert {row[0] for row in remaining} <= {"alembic_version"}
