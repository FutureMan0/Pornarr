"""Prometheus endpoint remains dark until enabled at runtime."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.settings import Setting
from pornarr_shared.metrics import measure
from tests.api.test_auth import create_user

pytest_plugins = ("tests.api.test_auth",)


async def test_metrics_are_disabled_by_default_then_expose_recorded_operations(app, client) -> None:
    await create_user(app)
    with measure("search"):
        pass

    disabled = await client.get("/api/metrics")
    assert disabled.status_code == 404, disabled.text

    async with AsyncSession(app.state.engine) as session:
        session.add(Setting(key="metrics_enabled", value=True))
        await session.commit()

    response = await client.get("/api/metrics")

    assert response.status_code == 200
    assert "pornarr_operations_total" in response.text
    assert 'operation="search"' in response.text
