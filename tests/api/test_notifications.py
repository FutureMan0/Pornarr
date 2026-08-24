"""In-app notification inbox, preferences, and live delivery."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.notification import Notification, NotificationKind
from pornarr_db.notifications import notify_user
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


class EventRedis:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.channels: list[str] = []

    async def xadd(self, _: str, fields: dict[str, object]) -> str:
        self.events.append((str(fields["type"]), fields))
        return "1-0"

    async def publish(self, channel: str, _: str) -> None:
        self.channels.append(channel)


async def test_users_can_read_and_manage_only_their_notification_inbox(app, client) -> None:
    user = await create_user(app)
    other = await create_user(app, username="other")
    async with AsyncSession(app.state.engine) as session:
        first = Notification(user_id=user.id, kind=NotificationKind.REQUEST_AVAILABLE, payload={})
        read = Notification(user_id=user.id, kind=NotificationKind.REQUEST_FAILED, payload={})
        outsider = Notification(
            user_id=other.id, kind=NotificationKind.REQUEST_AVAILABLE, payload={}
        )
        session.add_all([first, read, outsider])
        await session.flush()
        read_id = read.id
        first_id = first.id
        outsider_id = outsider.id
        await session.commit()

    await login(client, user.username, "correct horse battery staple")
    listed = await client.get("/api/notifications?unread=true")
    marked = await client.post(f"/api/notifications/{first_id}/read", headers=csrf_headers(client))
    forbidden = await client.post(
        f"/api/notifications/{outsider_id}/read", headers=csrf_headers(client)
    )
    all_read = await client.post("/api/notifications/read-all", headers=csrf_headers(client))

    assert {item["id"] for item in listed.json()} == {str(first_id), str(read_id)}
    assert marked.status_code == 200 and marked.json()["read_at"] is not None
    assert forbidden.status_code == 404
    assert all_read.status_code == 204
    assert (await client.get("/api/notifications?unread=true")).json() == []


async def test_preferences_gate_live_notification_delivery(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")
    disabled = await client.put(
        "/api/notifications/preferences/request_failed",
        json={"enabled": False},
        headers=csrf_headers(client),
    )

    redis = EventRedis()
    async with AsyncSession(app.state.engine) as session:
        blocked = await notify_user(
            session, redis, user_id=user.id, kind=NotificationKind.REQUEST_FAILED, payload={}
        )
        delivered = await notify_user(
            session,
            redis,
            user_id=user.id,
            kind=NotificationKind.REQUEST_AVAILABLE,
            payload={"request_id": str(UUID(int=1))},
        )

    assert disabled.status_code == 200
    assert blocked is None
    assert delivered is not None
    assert redis.events[0][0] == "notification"
    assert redis.channels == [f"pornarr:events:user:{user.id}"]


async def test_the_preference_list_answers_every_kind_with_what_it_is_set_to(app, client) -> None:
    """`GET /api/notifications/preferences` answered 500 for every caller.

    `dict(result.tuples())` reads as "the rows as a mapping" and is not: a
    `Result` carries `keys()`, so `dict` takes it for a mapping and indexes it,
    and `Result.__getitem__` does not exist. Nothing in the application had
    ever called the route, so the interpreter never reached the line.
    """

    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    default = await client.get("/api/notifications/preferences")
    assert default.status_code == 200
    assert {entry["kind"] for entry in default.json()} == {kind.value for kind in NotificationKind}
    assert all(entry["enabled"] for entry in default.json())

    await client.put(
        "/api/notifications/preferences/request_failed",
        json={"enabled": False},
        headers=csrf_headers(client),
    )

    after = await client.get("/api/notifications/preferences")
    assert after.status_code == 200
    assert {entry["kind"]: entry["enabled"] for entry in after.json()}["request_failed"] is False
