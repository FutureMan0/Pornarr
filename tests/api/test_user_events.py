"""Explicit account preference signals."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.entities import Performer, Tag
from pornarr_db.models.media import Media
from pornarr_db.models.playback import UserEvent
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def test_users_can_record_every_explicit_preference_signal(app, client) -> None:
    user = await create_user(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(title="Example", normalized_title="example")
        tag = Tag(name="Example tag", normalized_name="example tag")
        performer = Performer(name="Example performer", normalized_name="example performer")
        session.add_all((media, tag, performer))
        await session.commit()
    await login(client, user.username, "correct horse battery staple")

    payloads = [
        {"event_type": "favourite", "media_id": str(media.id)},
        {"event_type": "unfavourite", "media_id": str(media.id)},
        {"event_type": "not_interested", "media_id": str(media.id)},
        {"event_type": "hide_tag", "subject_id": str(tag.id)},
        {"event_type": "hide_performer", "subject_id": str(performer.id)},
    ]
    responses = [
        await client.post("/api/account/events", json=payload, headers=csrf_headers(client))
        for payload in payloads
    ]

    assert [response.status_code for response in responses] == [201, 201, 201, 201, 201]
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        events = list(
            await session.scalars(
                select(UserEvent).where(UserEvent.user_id == user.id).order_by(UserEvent.created_at)
            )
        )
    assert [event.event_type for event in events] == [payload["event_type"] for payload in payloads]
    assert all("query" not in event.__dict__ for event in events)


async def test_preference_events_require_their_relevant_target(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    missing_media = await client.post(
        "/api/account/events",
        json={"event_type": "favourite"},
        headers=csrf_headers(client),
    )
    missing_subject = await client.post(
        "/api/account/events",
        json={"event_type": "hide_tag"},
        headers=csrf_headers(client),
    )

    assert missing_media.status_code == 422
    assert missing_subject.status_code == 422
