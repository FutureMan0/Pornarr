"""Migrations against a real PostgreSQL.

These run against a real database rather than SQLite or a mock, because the
things that break — extensions, server defaults, constraint naming, type
comparison — are exactly the things a substitute does not reproduce.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

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
