"""Administrator-managed runtime settings."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media
from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def _media(app) -> Media:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(title="Example", normalized_title="example")
        session.add(media)
        await session.commit()
    return media


async def test_admin_changes_a_runtime_limit_without_restarting(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    defaults = await client.get("/api/admin/settings")

    assert defaults.status_code == 200
    assert defaults.json()["playback_completion_threshold_percent"] == 90
    assert defaults.json()["default_max_auto_downloads_per_day"] == 3
    assert defaults.json()["auto_download_recommendation_weight"] == 0.3
    assert defaults.json()["user_event_retention_days"] == 365
    assert defaults.json()["recommendation_tag_weight"] == 0.3

    updated = await client.patch(
        "/api/admin/settings",
        json={
            "playback_completion_threshold_percent": 75,
            "auto_download_recommendation_weight": 0.5,
            "user_event_retention_days": 30,
            "recommendation_tag_weight": 0.5,
        },
        headers=csrf_headers(client),
    )

    assert updated.status_code == 200
    assert updated.json()["playback_completion_threshold_percent"] == 75
    assert updated.json()["auto_download_recommendation_weight"] == 0.5
    assert updated.json()["user_event_retention_days"] == 30
    assert updated.json()["recommendation_tag_weight"] == 0.5
    assert app.state.redis.events[-1]["type"] == "settings.changed"
    assert json.loads(app.state.redis.events[-1]["data"]) == {
        "keys": [
            "auto_download_recommendation_weight",
            "playback_completion_threshold_percent",
            "recommendation_tag_weight",
            "user_event_retention_days",
        ]
    }

    media = await _media(app)
    progress = await client.post(
        f"/api/playback/{media.id}/progress",
        json={"position_seconds": 75, "duration_seconds": 100},
        headers=csrf_headers(client),
    )

    assert progress.status_code == 200
    assert progress.json()["completed"] is True


async def test_runtime_settings_reject_invalid_values_at_write_time(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.patch(
        "/api/admin/settings",
        json={"playback_completion_threshold_percent": 0},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert response.json()["context"]["fields"] == [
        {
            "location": ["body", "playback_completion_threshold_percent"],
            "problem": "Input should be greater than or equal to 1",
        }
    ]

    null_response = await client.patch(
        "/api/admin/settings",
        json={"playback_completion_threshold_percent": None},
        headers=csrf_headers(client),
    )

    assert null_response.status_code == 422
    assert null_response.json()["code"] == "VALIDATION_FAILED"


async def test_runtime_settings_are_administrator_only(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/settings")).status_code == 403


async def test_runtime_log_level_applies_without_restarting(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    previous_level = logging.getLogger().level
    try:
        response = await client.patch(
            "/api/admin/settings", json={"log_level": "debug"}, headers=csrf_headers(client)
        )
        assert response.status_code == 200
        assert response.json()["log_level"] == "debug"
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(previous_level)
