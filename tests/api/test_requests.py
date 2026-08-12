"""Request creation and management API behaviour."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import hash_password
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.playback import UserEvent
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import User, UserRole
from pornarr_db.requests import transition_request
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


async def test_users_can_create_specific_and_search_requests_then_filter_their_list(
    app, client
) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    specific = await client.post(
        "/api/requests",
        json={"query": "Example", "selected_release_guid": "release-1"},
        headers=csrf_headers(client),
    )
    search = await client.post(
        "/api/requests",
        json={"query": "Another example"},
        headers=csrf_headers(client),
    )

    assert specific.status_code == 201
    assert specific.json()["status"] == "queued"
    assert specific.json()["selected_release_guid"] == "release-1"
    assert search.status_code == 201
    assert search.json()["status"] == "searching"
    async with AsyncSession(app.state.engine) as session:
        events = list(await session.scalars(select(UserEvent).order_by(UserEvent.created_at)))
        scheduled = await session.get(Request, UUID(search.json()["id"]))
    assert [(event.event_type, event.value) for event in events] == [
        ("request", 80),
        ("request", 80),
    ]
    assert specific.json()["is_automatic"] is False
    assert scheduled is not None
    assert scheduled.next_search_at is not None
    assert scheduled.search_expires_at is not None
    assert scheduled.search_expires_at - scheduled.next_search_at == timedelta(days=90)
    assert (await client.get("/api/requests?status=searching")).json() == [
        {
            **search.json(),
            "history": [{"status": "searching"}],
        }
    ]


async def test_created_request_reaches_available_with_a_complete_lifecycle(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")
    created = await client.post(
        "/api/requests", json={"query": "Example"}, headers=csrf_headers(client)
    )
    request_id = UUID(created.json()["id"])

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        request = await session.get(Request, request_id)
        assert request is not None
        for status in (
            RequestStatus.RESULTS_FOUND,
            RequestStatus.QUEUED,
            RequestStatus.DOWNLOADING,
            RequestStatus.PROCESSING,
            RequestStatus.AVAILABLE,
        ):
            await transition_request(session, request, status)
        await session.commit()

    available = await client.get("/api/requests?status=available")

    assert created.status_code == 201
    assert available.json() == [
        {
            **created.json(),
            "status": "available",
            "history": [
                {"status": "searching"},
                {"status": "results_found"},
                {"status": "queued"},
                {"status": "downloading"},
                {"status": "processing"},
                {"status": "available"},
            ],
        }
    ]


async def test_users_only_see_and_manage_their_own_requests_while_admins_can_manage_all(
    app, client
) -> None:
    owner = await create_user(app, username="owner")
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        other = User(username="other", password_hash=hash_password("password"))
        session.add(other)
        await session.flush()
        request = Request(user_id=other.id, query="other request", status=RequestStatus.SEARCHING)
        session.add(request)
        await session.flush()
        session.add(RequestHistory(request_id=request.id, status=RequestStatus.SEARCHING))
        await session.commit()

    await login(client, owner.username, "correct horse battery staple")
    own = await client.post(
        "/api/requests", json={"query": "owner request"}, headers=csrf_headers(client)
    )

    assert [item["id"] for item in (await client.get("/api/requests")).json()] == [own.json()["id"]]
    assert (
        await client.post(f"/api/requests/{request.id}/cancel", headers=csrf_headers(client))
    ).status_code == 404

    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        await login(admin_client, admin.username, "correct horse battery staple")

        listed = await admin_client.get("/api/requests")
        cancelled = await admin_client.post(
            f"/api/requests/{request.id}/cancel", headers=csrf_headers(admin_client)
        )

    assert {item["id"] for item in listed.json()} == {own.json()["id"], str(request.id)}
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


async def test_active_request_quota_is_enforced_per_user_and_released_on_cancellation(
    app, client
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"request_max_active_per_user": 1})
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    first = await client.post(
        "/api/requests", json={"query": "first"}, headers=csrf_headers(client)
    )
    limited = await client.post(
        "/api/requests", json={"query": "second"}, headers=csrf_headers(client)
    )
    cancelled = await client.post(
        f"/api/requests/{first.json()['id']}/cancel", headers=csrf_headers(client)
    )
    retry = await client.post(
        "/api/requests", json={"query": "second"}, headers=csrf_headers(client)
    )

    assert first.status_code == 201
    assert limited.status_code == 409
    assert limited.json()["code"] == "REQUEST_QUOTA_EXCEEDED"
    assert cancelled.status_code == 200
    assert retry.status_code == 201


async def test_users_can_change_priority_and_retry_a_failed_request(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        request = Request(user_id=user.id, query="failed request", status=RequestStatus.FAILED)
        session.add(request)
        await session.flush()
        session.add(RequestHistory(request_id=request.id, status=RequestStatus.FAILED))
        await session.commit()

    await login(client, user.username, "correct horse battery staple")
    priority = await client.patch(
        f"/api/requests/{request.id}/priority", json={"priority": 80}, headers=csrf_headers(client)
    )
    retried = await client.post(f"/api/requests/{request.id}/retry", headers=csrf_headers(client))

    assert priority.status_code == 200
    assert priority.json()["priority"] == 80
    assert retried.status_code == 200
    assert retried.json()["status"] == "searching"
    assert [item["status"] for item in retried.json()["history"]] == ["failed", "searching"]


class RecordingCancellationAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def cancel(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        client_job_id: str,
    ) -> None:
        self.calls.append(
            {
                "host": host,
                "port": port,
                "url_base": url_base,
                "credentials": credentials,
                "client_job_id": client_job_id,
            }
        )


async def test_cancellation_is_propagated_to_the_download_client(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        download_client = DownloadClient(
            name="client",
            protocol="torrent",
            implementation="recording",
            host="client.example",
            port=8080,
            credentials="secret",
        )
        session.add(download_client)
        await session.flush()
        job = DownloadJob(
            download_client_id=download_client.id,
            client_name=download_client.name,
            protocol=download_client.protocol,
            release_guid="release-1",
            client_job_id="client-job-1",
            status="downloading",
        )
        session.add(job)
        await session.flush()
        request = Request(
            user_id=user.id,
            query="request",
            status=RequestStatus.DOWNLOADING,
            download_job_id=job.id,
        )
        session.add(request)
        await session.flush()
        session.add(RequestHistory(request_id=request.id, status=RequestStatus.DOWNLOADING))
        await session.commit()

    adapter = RecordingCancellationAdapter()
    app.state.download_client_adapters = {"recording": adapter}
    await login(client, user.username, "correct horse battery staple")

    response = await client.post(f"/api/requests/{request.id}/cancel", headers=csrf_headers(client))

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert adapter.calls == [
        {
            "host": "client.example",
            "port": 8080,
            "url_base": "",
            "credentials": "secret",
            "client_job_id": "client-job-1",
        }
    ]


def test_request_contract_declares_structured_errors(app) -> None:
    paths = app.openapi()["paths"]

    assert {"401", "409", "422"} <= paths["/api/requests"]["post"]["responses"].keys()
    assert {"401", "404", "409", "502"} <= paths["/api/requests/{request_id}/cancel"]["post"][
        "responses"
    ].keys()
