"""Administrator visibility into the measurements behind estimates."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.models.user import UserRole
from pornarr_db.statistics import record_measurement
from tests.api.test_auth import create_user, login

pytest_plugins = ("tests.api.test_auth",)


async def test_admin_can_inspect_measured_and_unknown_performance_inputs(app, client) -> None:
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")
    async with AsyncSession(app.state.engine) as session:
        await record_measurement(
            session,
            metric=PerformanceMetric.DOWNLOAD_SPEED,
            scope="usenet",
            value=123,
        )
        await session.commit()

    response = await client.get("/api/admin/performance")

    assert response.status_code == 200
    entries = {(entry["metric"], entry["scope"]): entry for entry in response.json()}
    assert entries[("download_speed", "usenet")] == {
        "metric": "download_speed",
        "scope": "usenet",
        "value": 123,
        "sample_count": 1,
    }
    assert entries[("download_speed", "torrent")] == {
        "metric": "download_speed",
        "scope": "torrent",
        "value": None,
        "sample_count": 0,
    }


async def test_performance_inputs_are_administrator_only(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/performance")).status_code == 403
