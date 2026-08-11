"""Authenticated storage-usage response behaviour."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.storage import record_daily_download
from tests.api.test_auth import create_user, login

pytest_plugins = ["tests.api.test_auth"]


async def test_account_storage_reports_the_authenticated_users_daily_usage(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    async with AsyncSession(app.state.engine) as session:
        await record_daily_download(session, user.id, 1234)
        await session.commit()

    response = await client.get("/api/account/storage")

    assert response.status_code == 200
    assert response.json()["downloaded_bytes"] == 1234
    assert response.json()["download_count"] == 1
    assert response.json()["reserved_bytes"] == 0
    assert response.json()["reserved_download_count"] == 0
