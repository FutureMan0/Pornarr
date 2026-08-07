from __future__ import annotations

from pathlib import Path

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from pornarr_api.main import create_app
from pornarr_shared.config import Settings
from pornarr_shared.jobs import WORKER_HEALTH_KEY


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


async def test_health_is_local_but_dependency_health_is_structured(tmp_path: Path) -> None:
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

    assert live.json() == {"status": "ok"}
    assert report.status_code == 200
    assert report.json()["status"] == "healthy"
