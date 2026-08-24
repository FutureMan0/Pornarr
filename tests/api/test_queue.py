"""Administrator queue management behaviour."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.download import DownloadJob
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.request import Request, RequestHistory, RequestStatus
from pornarr_db.models.user import UserRole
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


class RecordingQueueAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    async def pause(self, **_: object) -> None:
        self.calls.append(("pause", None))

    async def resume(self, **_: object) -> None:
        self.calls.append(("resume", None))

    async def set_priority(self, *, priority: int, **_: object) -> None:
        self.calls.append(("set_priority", priority))


async def test_admin_queue_operations_sync_the_client_and_recalculate_estimates(
    app, client
) -> None:
    user = await create_user(app)
    admin = await create_user(app, username="admin", role=UserRole.ADMIN)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        download_client = DownloadClient(
            name="client",
            protocol="usenet",
            implementation="recording",
            host="client.example",
            port=8080,
            credentials="secret",
        )
        session.add(download_client)
        await session.flush()
        automatic = DownloadJob(
            download_client_id=download_client.id,
            client_name=download_client.name,
            protocol=download_client.protocol,
            release_guid="automatic-release",
            client_job_id="automatic-job",
            status="queued",
            priority=40,
            estimated_seconds=100,
        )
        manual = DownloadJob(
            download_client_id=download_client.id,
            client_name=download_client.name,
            protocol=download_client.protocol,
            release_guid="manual-release",
            client_job_id="manual-job",
            status="queued",
            priority=20,
            estimated_seconds=50,
        )
        session.add_all([automatic, manual])
        await session.flush()
        request = Request(
            user_id=user.id,
            query="manual request",
            status=RequestStatus.QUEUED,
            priority=20,
            download_job_id=manual.id,
        )
        session.add(request)
        await session.flush()
        session.add(RequestHistory(request_id=request.id, status=RequestStatus.QUEUED))
        await session.commit()

    adapter = RecordingQueueAdapter()
    app.state.download_client_adapters = {"recording": adapter}
    await login(client, user.username, "correct horse battery staple")
    assert (await client.get("/api/queue")).status_code == 403

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        await login(admin_client, admin.username, "correct horse battery staple")
        initial = await admin_client.get("/api/queue?status=queued&sort=priority")
        first_page = await admin_client.get("/api/queue?status=queued&sort=priority&limit=1")
        second_page = await admin_client.get(
            "/api/queue",
            params={
                "status": "queued",
                "sort": "priority",
                "limit": 1,
                "cursor": first_page.json()["next_cursor"],
            },
        )
        priority = await admin_client.patch(
            f"/api/requests/{request.id}/priority",
            json={"priority": 100},
            headers=csrf_headers(admin_client),
        )
        after_priority = await admin_client.get("/api/queue?status=queued&sort=priority")
        paused = await admin_client.post(
            f"/api/requests/{request.id}/pause", headers=csrf_headers(admin_client)
        )
        resumed = await admin_client.post(
            f"/api/requests/{request.id}/resume", headers=csrf_headers(admin_client)
        )

    assert initial.status_code == 200
    assert [item["id"] for item in initial.json()["items"]] == [str(automatic.id), str(manual.id)]
    assert [item["queue_estimate"]["low_seconds"] for item in initial.json()["items"]] == [80, 120]
    assert initial.json()["next_cursor"] is None
    assert [item["id"] for item in first_page.json()["items"]] == [str(automatic.id)]
    assert first_page.json()["next_cursor"] is not None
    assert [item["id"] for item in second_page.json()["items"]] == [str(manual.id)]
    assert second_page.json()["next_cursor"] is None
    assert priority.status_code == 200
    assert after_priority.status_code == 200
    assert [item["id"] for item in after_priority.json()["items"]] == [
        str(manual.id),
        str(automatic.id),
    ]
    assert [item["queue_estimate"]["low_seconds"] for item in after_priority.json()["items"]] == [
        40,
        120,
    ]
    assert paused.status_code == resumed.status_code == 200
    assert adapter.calls == [
        ("set_priority", 100),
        ("pause", None),
        ("resume", None),
    ]
    async with AsyncSession(app.state.engine) as session:
        persisted = await session.get(DownloadJob, manual.id)
    assert persisted is not None
    assert persisted.priority == 100
    assert persisted.status == "queued"


async def test_the_summary_counts_the_whole_queue_not_a_page(app, client) -> None:
    """The cards above the queue must not change when someone scrolls."""
    admin = await create_user(app, username="root", role=UserRole.ADMIN)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        for index in range(3):
            session.add(
                DownloadJob(
                    client_name="client",
                    protocol="usenet",
                    release_guid=f"queued-{index}",
                    status="queued",
                )
            )
        session.add(
            DownloadJob(
                client_name="client",
                protocol="usenet",
                release_guid="moving",
                status="downloading",
                download_speed_bytes=1_000,
            )
        )
        session.add(
            DownloadJob(
                client_name="client",
                protocol="usenet",
                release_guid="also-moving",
                status="moving",
                download_speed_bytes=500,
            )
        )
        session.add(
            DownloadJob(
                client_name="client", protocol="usenet", release_guid="broken", status="failed"
            )
        )
        await session.commit()

    await login(client, admin.username, "correct horse battery staple")
    body = (await client.get("/api/queue/summary")).json()

    assert body == {
        "active": 2,
        "queued": 3,
        "failed": 1,
        "completed": 0,
        # Only the jobs actually moving bytes. A paused job reports no speed and
        # must not be summed in as a zero that drags the figure down.
        "speed_bytes": 1_500,
    }


async def test_a_queued_job_carries_what_somebody_asked_for(app, client) -> None:
    admin = await create_user(app, username="root", role=UserRole.ADMIN)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        job = DownloadJob(
            client_name="client", protocol="usenet", release_guid="guid-1", status="queued"
        )
        session.add(job)
        await session.flush()
        session.add(
            Request(
                user_id=admin.id,
                query="The Long Way",
                status=RequestStatus.DOWNLOADING,
                download_job_id=job.id,
            )
        )
        await session.commit()

    await login(client, admin.username, "correct horse battery staple")
    items = (await client.get("/api/queue")).json()["items"]

    # A queue listing release GUIDs is a queue nobody can read.
    assert items[0]["title"] == "The Long Way"


async def test_a_job_with_no_request_says_so_rather_than_inventing_a_name(app, client) -> None:
    admin = await create_user(app, username="root", role=UserRole.ADMIN)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        session.add(
            DownloadJob(
                client_name="client", protocol="usenet", release_guid="orphan", status="queued"
            )
        )
        await session.commit()

    await login(client, admin.username, "correct horse battery staple")
    items = (await client.get("/api/queue")).json()["items"]

    assert items[0]["title"] is None
    # The GUID is all there is, and it is still there for the client to show.
    assert items[0]["release_guid"] == "orphan"
