"""Liveness and dependency health endpoints."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from pornarr_shared.jobs import WORKER_HEALTH_KEY

router = APIRouter(tags=["health"])


class ComponentHealth(BaseModel):
    status: Literal["healthy", "unhealthy"]
    detail: str | None = None


class HealthReport(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    database: ComponentHealth
    redis: ComponentHealth
    worker: ComponentHealth
    filesystem: ComponentHealth
    backup: ComponentHealth | None = None


async def _database(request: Request) -> ComponentHealth:
    try:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return ComponentHealth(status="unhealthy", detail="database query failed")
    return ComponentHealth(status="healthy")


async def _redis(request: Request) -> ComponentHealth:
    try:
        await request.app.state.redis.ping()
    except Exception:
        return ComponentHealth(status="unhealthy", detail="redis ping failed")
    return ComponentHealth(status="healthy")


async def _worker(request: Request) -> ComponentHealth:
    try:
        heartbeat = await request.app.state.redis.get(WORKER_HEALTH_KEY)
    except Exception:
        return ComponentHealth(status="unhealthy", detail="worker heartbeat unavailable")
    if heartbeat is None:
        return ComponentHealth(status="unhealthy", detail="worker heartbeat missing")
    return ComponentHealth(status="healthy")


def _filesystem(paths: tuple[Path, ...], minimum_free_percent: int) -> ComponentHealth:
    try:
        devices = {path: path.stat().st_dev for path in paths}
        first_path, first_device = next(iter(devices.items()))
        for path, device in devices.items():
            if device != first_device:
                return ComponentHealth(
                    status="unhealthy", detail=f"separate mounts: {first_path} and {path}"
                )
        usage = shutil.disk_usage(first_path)
    except OSError:
        return ComponentHealth(status="unhealthy", detail="data path is unavailable")
    free_percent = usage.free * 100 / usage.total
    if free_percent < minimum_free_percent:
        return ComponentHealth(
            status="unhealthy", detail="free disk space is below the configured minimum"
        )
    return ComponentHealth(status="healthy")


def _backup(path: Path, max_age_hours: int | None) -> ComponentHealth | None:
    if max_age_hours is None:
        return None
    try:
        newest = max(
            (backup.stat().st_mtime for backup in path.glob("*.dump") if backup.is_file()),
            default=None,
        )
    except OSError:
        return ComponentHealth(status="unhealthy", detail="backup directory is unavailable")
    if newest is None:
        return ComponentHealth(status="unhealthy", detail="backup dump is missing")
    age = datetime.now(UTC) - datetime.fromtimestamp(newest, UTC)
    if age > timedelta(hours=max_age_hours):
        return ComponentHealth(
            status="unhealthy", detail=f"latest backup is older than {max_age_hours} hours"
        )
    return ComponentHealth(
        status="healthy", detail=f"latest backup is {int(age.total_seconds() // 60)} minutes old"
    )


@router.get("/health", response_model=HealthReport)
async def dependency_health(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    database = await _database(request)
    redis = await _redis(request)
    worker = await _worker(request)
    filesystem = _filesystem(
        (
            settings.torrents_path,
            settings.usenet_path,
            settings.library_path,
            settings.quarantine_path,
            settings.thumbnail_path,
            settings.transcode_path,
        ),
        settings.min_free_disk_percent,
    )
    backup = _backup(settings.backup_path, settings.backup_max_age_hours)
    status = "healthy"
    if database.status == "unhealthy" or filesystem.status == "unhealthy":
        status = "unhealthy"
    elif (
        redis.status == "unhealthy"
        or worker.status == "unhealthy"
        or (backup is not None and backup.status == "unhealthy")
    ):
        status = "degraded"
    report = HealthReport(
        status=status,
        database=database,
        redis=redis,
        worker=worker,
        filesystem=filesystem,
        backup=backup,
    )
    return JSONResponse(
        status_code=503 if status == "unhealthy" else 200,
        content=report.model_dump(exclude_none=True),
    )
