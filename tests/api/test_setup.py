"""First-run setup locks the instance until an admin is created."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.download_clients import route_download_client
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.filters import ContentFilterProfile, ContentFilterRule, FilterProfileScope
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.metadata_provider import MetadataProvider
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User
from pornarr_db.types import set_cipher
from pornarr_integrations.indexers import IndexerCategory
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import login

pytest_plugins = ("tests.api.test_auth",)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


class WorkingIndexerAdapter:
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        assert base_url == "https://indexer.example"
        assert api_key == "indexer-key"
        return [IndexerCategory("5000", "TV")]


class FailingIndexerAdapter:
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        raise ConnectionError(f"{base_url} rejected {api_key}")


class WorkingDownloadClientAdapter:
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        assert (host, port, url_base, credentials) == ("client.example", 8080, "", "client-key")


class FailingDownloadClientAdapter:
    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        raise ConnectionError("refused")


async def test_fresh_instance_serves_only_setup_then_unlocks(app, client, tmp_path: Path) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()

    assert (await client.get("/api/auth/me")).status_code == 503
    assert (
        await client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    ).status_code == 503
    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )

    assert completed.status_code == 201
    assert completed.json()["same_filesystem_as_downloads"] is True
    assert completed.json()["warning"] is None
    async with app.state.engine.connect() as connection:
        assert await connection.scalar(select(User.id)) is not None
        assert await connection.scalar(select(AutomationRule.user_id)) is not None
        assert await connection.scalar(select(RootFolder.id)) is not None
        profile_id = await connection.scalar(
            select(ContentFilterProfile.id).where(
                ContentFilterProfile.scope == FilterProfileScope.GLOBAL
            )
        )
        assert profile_id is not None
        rule_count = await connection.scalar(
            select(func.count(ContentFilterRule.id)).where(
                ContentFilterRule.profile_id == profile_id
            )
        )
        assert rule_count == 6
        enabled_rule_count = await connection.scalar(
            select(func.count(ContentFilterRule.id)).where(
                ContentFilterRule.profile_id == profile_id,
                ContentFilterRule.enabled.is_(True),
            )
        )
        assert enabled_rule_count == 0
    repeated = await client.post(
        "/api/setup/complete",
        json={
            "username": "second-admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "SETUP_ALREADY_COMPLETED"
    validation_after_setup = await client.post(
        "/api/setup/validate-library-path", json={"library_path": str(library_path)}
    )
    assert validation_after_setup.status_code == 409
    assert validation_after_setup.json()["code"] == "SETUP_ALREADY_COMPLETED"
    await login(client, "admin", "correct horse battery staple")
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_setup_reports_a_different_filesystem(
    app, client, tmp_path: Path, monkeypatch
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    torrents_path = tmp_path / "torrents"
    library_path = tmp_path / "library"
    torrents_path.mkdir()
    library_path.mkdir()
    original_stat = Path.stat

    def stat_on_other_device(path: Path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == library_path:
            return SimpleNamespace(st_dev=result.st_dev + 1, st_mode=result.st_mode)
        return result

    monkeypatch.setattr(Path, "stat", stat_on_other_device)

    response = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )

    assert response.status_code == 201
    assert response.json()["same_filesystem_as_downloads"] is False
    assert (
        response.json()["warning"] == "different filesystem from downloads; imports cannot hardlink"
    )


async def test_setup_validates_a_library_path_without_configuring_the_instance(
    app, client, tmp_path: Path, monkeypatch
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()
    original_stat = Path.stat

    def stat_on_other_device(path: Path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == library_path:
            return SimpleNamespace(st_dev=result.st_dev + 1, st_mode=result.st_mode)
        return result

    monkeypatch.setattr(Path, "stat", stat_on_other_device)

    response = await client.post(
        "/api/setup/validate-library-path", json={"library_path": str(library_path)}
    )

    assert response.status_code == 200
    assert response.json() == {
        "same_filesystem_as_downloads": False,
        "warning": "different filesystem from downloads; imports cannot hardlink",
    }
    assert (await client.get("/api/setup/status")).json() == {"configured": False}


async def test_setup_accepts_the_data_root_before_download_directories_exist(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})

    response = await client.post(
        "/api/setup/validate-library-path", json={"library_path": str(tmp_path)}
    )

    assert response.status_code == 200
    assert response.json() == {"same_filesystem_as_downloads": True, "warning": None}


async def test_an_indexer_connection_can_be_tested_before_an_admin_exists(app, client) -> None:
    app.state.indexer_adapters = {"torznab": WorkingIndexerAdapter()}

    response = await client.post(
        "/api/setup/test-indexer",
        json={
            "implementation": "torznab",
            "base_url": "https://indexer.example",
            "api_key": "indexer-key",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"categories": [{"id": "5000", "name": "TV"}]}


async def test_an_indexer_connection_failure_is_reported_and_redacted(app, client) -> None:
    app.state.indexer_adapters = {"torznab": FailingIndexerAdapter()}

    response = await client.post(
        "/api/setup/test-indexer",
        json={
            "implementation": "torznab",
            "base_url": "https://indexer.example",
            "api_key": "indexer-key",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INDEXER_CONNECTION_FAILED"
    assert "indexer-key" not in response.text


async def test_a_download_client_connection_can_be_tested_before_an_admin_exists(
    app, client
) -> None:
    app.state.download_client_adapters = {"qbittorrent": WorkingDownloadClientAdapter()}

    response = await client.post(
        "/api/setup/test-download-client",
        json={
            "implementation": "qbittorrent",
            "host": "client.example",
            "port": 8080,
            "credentials": "client-key",
        },
    )

    assert response.status_code == 204


async def test_a_download_client_connection_failure_is_reported(app, client) -> None:
    app.state.download_client_adapters = {"qbittorrent": FailingDownloadClientAdapter()}

    response = await client.post(
        "/api/setup/test-download-client",
        json={
            "implementation": "qbittorrent",
            "host": "client.example",
            "port": 8080,
            "credentials": "client-key",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "DOWNLOAD_CLIENT_CONNECTION_FAILED"


async def test_the_new_test_endpoints_refuse_once_the_instance_is_configured(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()
    app.state.indexer_adapters = {"torznab": WorkingIndexerAdapter()}
    app.state.download_client_adapters = {"qbittorrent": WorkingDownloadClientAdapter()}
    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )
    assert completed.status_code == 201

    indexer_response = await client.post(
        "/api/setup/test-indexer",
        json={
            "implementation": "torznab",
            "base_url": "https://indexer.example",
            "api_key": "indexer-key",
        },
    )
    client_response = await client.post(
        "/api/setup/test-download-client",
        json={
            "implementation": "qbittorrent",
            "host": "client.example",
            "port": 8080,
            "credentials": "client-key",
        },
    )

    assert indexer_response.status_code == 409
    assert indexer_response.json()["code"] == "SETUP_ALREADY_COMPLETED"
    assert client_response.status_code == 409
    assert client_response.json()["code"] == "SETUP_ALREADY_COMPLETED"


async def test_completing_setup_configures_an_indexer_a_download_client_and_a_provider(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()
    app.state.indexer_adapters = {"torznab": WorkingIndexerAdapter()}
    app.state.download_client_adapters = {"qbittorrent": WorkingDownloadClientAdapter()}

    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
            "indexer": {
                "implementation": "torznab",
                "base_url": "https://indexer.example",
                "api_key": "indexer-key",
            },
            "download_client": {
                "implementation": "qbittorrent",
                "host": "client.example",
                "port": 8080,
                "credentials": "client-key",
                "category": "pornarr",
            },
            "metadata_provider": {
                "implementation": "stashdb",
                "api_key": "metadata-key",
            },
        },
    )

    assert completed.status_code == 201
    assert "indexer-key" not in completed.text
    assert "client-key" not in completed.text
    assert "metadata-key" not in completed.text
    async with AsyncSession(app.state.engine) as session:
        indexer = await session.scalar(select(Indexer))
        assert indexer is not None
        assert indexer.implementation == "torznab"
        assert indexer.protocol == "torrent"
        assert indexer.api_key == "indexer-key"
        assert indexer.categories == [{"id": "5000", "name": "TV"}]
        assert indexer.health == "healthy"
        stats = await session.get(IndexerStats, indexer.id)
        assert stats is not None

        provider = await session.scalar(select(MetadataProvider))
        assert provider is not None
        assert provider.implementation == "stashdb"
        assert provider.api_key == "metadata-key"

        # Routable immediately: setup's whole point is a working instance, and
        # route_download_client only returns a client whose health is healthy.
        routed = await route_download_client(session, "torrent")
        assert routed.credentials == "client-key"
        assert routed.category == "pornarr"


async def test_completing_setup_rolls_back_everything_if_the_indexer_never_answers(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()
    app.state.indexer_adapters = {"torznab": FailingIndexerAdapter()}

    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
            "indexer": {
                "implementation": "torznab",
                "base_url": "https://indexer.example",
                "api_key": "indexer-key",
            },
        },
    )

    assert completed.status_code == 422
    assert completed.json()["code"] == "INDEXER_CONNECTION_FAILED"
    async with app.state.engine.connect() as connection:
        # Nothing partially configured: the administrator that would have made
        # the instance unrecoverable-by-wizard was never created either.
        assert await connection.scalar(select(User.id)) is None
        assert await connection.scalar(select(Indexer.id)) is None


async def test_setup_completes_without_any_of_the_optional_integrations(
    app, client, tmp_path: Path
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"data_path": tmp_path})
    app.state.settings.torrents_path.mkdir()
    library_path = tmp_path / "library"
    library_path.mkdir()

    completed = await client.post(
        "/api/setup/complete",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
            "library_path": str(library_path),
        },
    )

    assert completed.status_code == 201
    async with app.state.engine.connect() as connection:
        assert await connection.scalar(select(Indexer.id)) is None
        assert await connection.scalar(select(DownloadClient.id)) is None
        assert await connection.scalar(select(MetadataProvider.id)) is None
