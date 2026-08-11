"""Local library search behaviour."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.routers import search as search_router
from pornarr_db.media_search import MediaSearchResult
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.playback import UserEvent
from pornarr_shared.metrics import REGISTRY
from tests.api.test_auth import create_user, login

pytest_plugins = ["tests.api.test_auth"]


async def test_local_search_honours_the_requesting_users_filter_profile(
    app, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = {"operation": "search", "result": "ok"}
    before = REGISTRY.get_sample_value("pornarr_operations_total", labels=labels)
    assert before is not None
    user = await create_user(app)
    media = Media(
        id=uuid4(),
        title="Hidden Summer Scene",
        normalized_title="hidden summer scene",
    )
    media_file = MediaFile(
        id=uuid4(), media_id=media.id, path="/library/hidden.mp4", size=1_000, container="mp4"
    )

    async def fake_search_media(*_) -> list[MediaSearchResult]:
        return [MediaSearchResult(media=media, media_file=media_file, relevance=0.9)]

    monkeypatch.setattr(search_router, "search_media", fake_search_media)
    async with AsyncSession(app.state.engine) as session:
        profile = ContentFilterProfile(scope=FilterProfileScope.USER, user_id=user.id)
        profile.rules.append(
            ContentFilterRule(
                kind=FilterRuleKind.TERM,
                pattern="hidden",
                action=FilterAction.REJECT,
                enabled=True,
            )
        )
        session.add(profile)
        await session.commit()

    await login(client, user.username, "correct horse battery staple")

    response = await client.get("/api/search/local", params={"q": "summer"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    assert REGISTRY.get_sample_value("pornarr_operations_total", labels=labels) == before + 1
    async with AsyncSession(app.state.engine) as session:
        event = await session.scalar(select(UserEvent))
    assert event is not None
    assert event.event_type == "search"
    assert event.value == 0
