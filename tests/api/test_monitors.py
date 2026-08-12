"""User-owned monitors for performer, studio, and saved-query discovery."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import Performer, Studio
from pornarr_db.models.monitor import Monitor
from pornarr_db.models.quality import QualityDefinition, QualityProfile
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def _quality_profile(session: AsyncSession) -> QualityProfile:
    quality = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=1,
        maximum_size_mb_per_minute=100,
    )
    session.add(quality)
    await session.flush()
    profile = QualityProfile(name="Default", cutoff_quality_id=quality.id, is_default=True)
    session.add(profile)
    await session.flush()
    return profile


async def test_user_can_create_list_edit_and_delete_monitors(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        performer = Performer(name="Example Performer", normalized_name="example performer")
        studio = Studio(name="Example Studio", normalized_name="example studio")
        session.add_all((performer, studio))
        profile = await _quality_profile(session)
        await session.commit()

    await login(client, user.username, "correct horse battery staple")
    performer_response = await client.post(
        "/api/monitors",
        json={"kind": "performer", "performer_id": str(performer.id)},
        headers=csrf_headers(client),
    )
    studio_response = await client.post(
        "/api/monitors",
        json={
            "kind": "studio",
            "studio_id": str(studio.id),
            "quality_profile_id": str(profile.id),
            "minimum_score": 25,
        },
        headers=csrf_headers(client),
    )
    query_response = await client.post(
        "/api/monitors",
        json={"kind": "query", "query": "  New  Releases  "},
        headers=csrf_headers(client),
    )

    assert performer_response.status_code == 201
    assert performer_response.json() == {
        "id": performer_response.json()["id"],
        "kind": "performer",
        "performer_id": str(performer.id),
        "studio_id": None,
        "query": None,
        "quality_profile_id": str(profile.id),
        "enabled": True,
        "minimum_score": 0,
        "last_match_at": None,
    }
    assert studio_response.status_code == 201
    assert query_response.status_code == 201
    assert query_response.json()["query"] == "New  Releases"
    duplicate = await client.post(
        "/api/monitors",
        json={"kind": "performer", "performer_id": str(performer.id)},
        headers=csrf_headers(client),
    )

    listed = await client.get("/api/monitors")
    changed = await client.patch(
        f"/api/monitors/{query_response.json()['id']}",
        json={"query": "More releases", "enabled": False, "minimum_score": 40},
        headers=csrf_headers(client),
    )
    deleted = await client.delete(
        f"/api/monitors/{studio_response.json()['id']}", headers=csrf_headers(client)
    )

    assert listed.status_code == 200
    assert {item["kind"] for item in listed.json()} == {"performer", "studio", "query"}
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "MONITOR_ALREADY_EXISTS"
    assert changed.status_code == 200
    assert changed.json()["query"] == "More releases"
    assert changed.json()["enabled"] is False
    assert changed.json()["minimum_score"] == 40
    assert deleted.status_code == 204


async def test_users_cannot_manage_each_others_monitors_and_targets_cascade(app, client) -> None:
    owner = await create_user(app, username="owner")
    other = await create_user(app, username="other")
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        performer = Performer(name="Example Performer", normalized_name="example performer")
        session.add(performer)
        await _quality_profile(session)
        await session.commit()

    await login(client, owner.username, "correct horse battery staple")
    created = await client.post(
        "/api/monitors",
        json={"kind": "performer", "performer_id": str(performer.id)},
        headers=csrf_headers(client),
    )
    monitor_id = UUID(created.json()["id"])

    await login(client, other.username, "correct horse battery staple")
    forbidden = await client.delete(f"/api/monitors/{monitor_id}", headers=csrf_headers(client))

    assert forbidden.status_code == 404
    async with AsyncSession(app.state.engine) as session:
        await session.execute(text("PRAGMA foreign_keys = ON"))
        await session.delete(await session.get(Performer, performer.id))
        await session.commit()
        assert await session.scalar(select(Monitor).where(Monitor.id == monitor_id)) is None


async def test_user_can_trigger_a_monitor_backlog_search(app, client, monkeypatch) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        await _quality_profile(session)
        await session.commit()
    await login(client, user.username, "correct horse battery staple")
    created = await client.post(
        "/api/monitors",
        json={"kind": "query", "query": "Example Performer"},
        headers=csrf_headers(client),
    )
    calls: list[dict[str, str]] = []

    async def enqueue_job(function: str, *args: object, **kwargs: object) -> dict[str, object]:
        monitor_id, run_id = args
        queue = kwargs["_queue_name"]
        assert isinstance(monitor_id, str)
        assert isinstance(run_id, str)
        assert isinstance(queue, str)
        calls.append(
            {"function": function, "monitor_id": monitor_id, "run_id": run_id, "queue": queue}
        )
        return {}

    monkeypatch.setattr(app.state.redis, "enqueue_job", enqueue_job, raising=False)

    response = await client.post(
        f"/api/monitors/{created.json()['id']}/backlog-search",
        headers=csrf_headers(client),
    )

    assert response.status_code == 202
    assert calls[0]["function"] == "backlog_search"
    assert calls[0]["monitor_id"] == created.json()["id"]
    assert calls[0]["run_id"].startswith("manual:")
    assert calls[0]["queue"] == "pornarr:indexer"
