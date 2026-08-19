from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from pornarr_api.lifespan import _warn_about_separate_mounts
from pornarr_api.main import create_app
from pornarr_shared.config import Settings
from pornarr_shared.jobs import WORKER_HEALTH_KEY

#: tmpfs, so `st_dev` differs from anything on disk without needing root. This is
#: the only way to make the ADR 0004 comparison fire in a unit test; the live
#: version, on a container with a tmpfs over its library, is in
#: `tests/e2e/setup-probe.spec.ts`.
SHARED_MEMORY = Path("/dev/shm")


class Redis:
    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return "alive" if key == WORKER_HEALTH_KEY else None


class Connection:
    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, _: object) -> None:
        return None


class Engine:
    def connect(self) -> Connection:
        return Connection()


class BrokenEngine:
    def connect(self) -> Connection:
        raise ConnectionError("the database is not there")


async def test_both_health_addresses_answer_the_same_structured_report(tmp_path: Path) -> None:
    for name in ("torrents", "usenet", "library", "quarantine", "thumbnails", "transcodes"):
        (tmp_path / name).mkdir()
    settings = Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
        data_path=tmp_path,
    )
    app = create_app(settings)
    app.state.engine = Engine()
    app.state.redis = Redis()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health")
        report = await client.get("/api/health")

    # Both addresses answer the same report. `/health` is the one
    # `docker-compose.yml` curls and the one backup.md ends a restore with, and it
    # used to be a lambda returning a literal — an endpoint that verifies a
    # deployment has to be able to say no.
    assert report.status_code == 200
    assert report.json()["status"] == "healthy"
    assert live.status_code == 200
    assert live.json() == report.json()


async def test_the_container_probe_fails_when_the_instance_is_unhealthy(tmp_path: Path) -> None:
    """`/health` can say no. Until it could, `curl --fail .../health` proved nothing."""
    for name in ("torrents", "usenet", "library", "quarantine", "thumbnails", "transcodes"):
        (tmp_path / name).mkdir()
    settings = Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
        data_path=tmp_path,
    )
    app = create_app(settings)
    app.state.engine = BrokenEngine()
    app.state.redis = Redis()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health")
        report = await client.get("/api/health")

    assert live.status_code == 503
    assert live.json()["status"] == "unhealthy"
    assert live.json()["database"] == {"status": "unhealthy", "detail": "database query failed"}
    assert live.json() == report.json()


async def test_health_surfaces_a_configured_backup_age(tmp_path: Path) -> None:
    for name in ("torrents", "usenet", "library", "quarantine", "thumbnails", "transcodes"):
        (tmp_path / name).mkdir()
    backup_path = tmp_path / "backups"
    backup_path.mkdir()
    backup = backup_path / "pornarr-test.dump"
    backup.write_text("dump")
    settings = Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
        data_path=tmp_path,
        backup_path=backup_path,
        backup_max_age_hours=1,
    )
    app = create_app(settings)
    app.state.engine = Engine()
    app.state.redis = Redis()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        healthy = await client.get("/api/health")
        os.utime(backup, (time.time() - 7200, time.time() - 7200))
        stale = await client.get("/api/health")

    assert healthy.json()["backup"]["status"] == "healthy"
    assert stale.status_code == 200
    assert stale.json()["status"] == "degraded"
    assert stale.json()["backup"]["status"] == "unhealthy"


def _two_device_data_path(root: Path) -> Settings:
    """A settings object whose library really is on another device.

    `library` is a symlink into tmpfs; `Path.stat()` follows it, which is what the
    check reads. Everything else stays on `root`.
    """
    for name in ("torrents", "usenet", "quarantine", "thumbnails", "transcodes"):
        (root / name).mkdir()
    elsewhere = SHARED_MEMORY / f"pornarr-test-{root.name}"
    elsewhere.mkdir(exist_ok=True)
    (root / "library").symlink_to(elsewhere, target_is_directory=True)
    return Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
        data_path=root,
    )


def test_the_startup_check_warns_loudly_when_the_data_paths_straddle_two_mounts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0004 L12, deployment.md L31-32, troubleshooting.md L4-6.

    All three tell an operator that the device identifiers are compared *at
    startup* and that a mismatch warns loudly, and troubleshooting.md tells them
    to go and read that warning when imports copy. There was no such line: a
    mis-mounted instance reached `running` with nothing in its log.
    """
    if not SHARED_MEMORY.is_dir():
        pytest.skip("no tmpfs at /dev/shm to put a second device behind")
    settings = _two_device_data_path(tmp_path)
    # Read from the kernel, not from `separate_mounts`: a precondition checked
    # with the function under test is a test that skips itself the moment that
    # function breaks.
    if settings.torrents_path.stat().st_dev == settings.library_path.stat().st_dev:
        pytest.skip("/dev/shm is on the same device as the temporary directory")

    with caplog.at_level(logging.WARNING, logger="pornarr_api.lifespan"):
        _warn_about_separate_mounts(settings)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    # Which two paths disagree, not merely that two of them do: six directories
    # and "one of these is wrong" is not a warning an operator can act on.
    assert str(settings.torrents_path) in message
    assert str(settings.library_path) in message
    assert "hardlink" in message


def test_the_startup_check_says_nothing_when_one_volume_carries_everything(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The negative space. A correctly mounted instance must start in silence."""
    for name in ("torrents", "usenet", "library", "quarantine", "thumbnails", "transcodes"):
        (tmp_path / name).mkdir()
    settings = Settings(
        app_secret=SecretStr("a" * 32),
        database_url="postgresql+psycopg://example",
        redis_url="redis://example",
        data_path=tmp_path,
    )

    with caplog.at_level(logging.WARNING, logger="pornarr_api.lifespan"):
        _warn_about_separate_mounts(settings)

    assert caplog.records == []
