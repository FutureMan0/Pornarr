"""The endpoints the new screens depend on.

Concentrated on the boundaries: whether a private library actually stays
private, whether pooled search leaks whose copy it found, and whether comment
anonymity survives a client that would happily render a name.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.routers import search as search_router
from pornarr_db.media_search import MediaSearchResult
from pornarr_db.models.media import Media, MediaFile
from pornarr_db.models.scene_marker import SceneMarker
from pornarr_db.models.settings import Setting
from pornarr_db.models.user import User, UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)

PASSWORD = "correct horse battery staple"


async def _media(app: FastAPI, title: str, *, owner: User | None = None) -> Media:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(
            title=title,
            normalized_title=title.casefold(),
            owner_id=owner.id if owner else None,
        )
        session.add(media)
        await session.flush()
        session.add(
            MediaFile(
                media_id=media.id,
                path=f"/data/{title}.mkv",
                size=1024,
                duration_seconds=600,
                is_active=True,
            )
        )
        await session.commit()
    return media


async def _set_flag(app: FastAPI, key: str, value: bool) -> None:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        session.add(Setting(key=key, value=value))
        await session.commit()


async def test_a_private_library_shows_only_your_own_titles(
    app: FastAPI, client: AsyncClient
) -> None:
    alice = await create_user(app, username="alice")
    bob = await create_user(app, username="bob")
    await _media(app, "Alice title", owner=alice)
    await _media(app, "Bob title", owner=bob)
    shared = await _media(app, "Shared title")
    await _set_flag(app, "private_libraries", True)

    await login(client, "alice", PASSWORD)
    titles = [item["title"] for item in (await client.get("/api/library")).json()["items"]]

    # The unowned title is the pool every server starts with; scoping must not
    # make a library that predates ownership disappear.
    assert sorted(titles) == ["Alice title", "Shared title"]
    assert shared.owner_id is None


async def test_an_administrator_sees_the_whole_pool(app: FastAPI, client: AsyncClient) -> None:
    alice = await create_user(app, username="alice")
    await _media(app, "Alice title", owner=alice)
    await create_user(app, username="root", role=UserRole.ADMIN)
    await _set_flag(app, "private_libraries", True)

    await login(client, "root", PASSWORD)
    titles = [item["title"] for item in (await client.get("/api/library")).json()["items"]]

    assert titles == ["Alice title"]


async def _fake_local_search(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, media: list[Media]
) -> None:
    """Stand in for the trigram query, which needs PostgreSQL.

    Matches how `tests/api/test_search.py` handles the same problem: the
    scoping being tested here happens after the query, so replacing the query
    exercises exactly the part that is new.
    """
    files = {
        item.id: MediaFile(id=uuid4(), media_id=item.id, path=f"/library/{item.id}", size=1000)
        for item in media
    }

    async def fake_search_media(*_) -> list[MediaSearchResult]:
        return [
            MediaSearchResult(media=item, media_file=files[item.id], relevance=0.9)
            for item in media
        ]

    monkeypatch.setattr(search_router, "search_media", fake_search_media)


async def test_pooled_search_finds_the_other_copy_without_naming_its_owner(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of a household server: learn the house has it, not whose it is."""

    alice = await create_user(app, username="alice")
    bob = await create_user(app, username="bob")
    mine = await _media(app, "Aurora", owner=alice)
    theirs = await _media(app, "Aurora two", owner=bob)
    await _set_flag(app, "private_libraries", True)
    await _fake_local_search(app, monkeypatch, [mine, theirs])

    await login(client, "alice", PASSWORD)
    items = (await client.get("/api/search/local", params={"q": "Aurora"})).json()["items"]

    assert len(items) == 2
    assert {item["in_my_library"] for item in items} == {True, False}
    assert all("owner_id" not in item for item in items)


async def test_turning_pooled_search_off_hides_the_other_libraries(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user(app, username="alice")
    bob = await create_user(app, username="bob")
    mine = await _media(app, "Aurora", owner=alice)
    theirs = await _media(app, "Aurora two", owner=bob)
    await _set_flag(app, "private_libraries", True)
    await _set_flag(app, "pooled_search", False)
    await _fake_local_search(app, monkeypatch, [mine, theirs])

    await login(client, "alice", PASSWORD)
    items = (await client.get("/api/search/local", params={"q": "Aurora"})).json()["items"]

    assert [item["in_my_library"] for item in items] == [True]


async def test_the_library_can_be_filtered_by_rating(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app, username="alice")
    good = await _media(app, "Good")
    await _media(app, "Unrated")
    await login(client, "alice", PASSWORD)
    await client.put(
        f"/api/media/{good.id}/rating", json={"stars": 5}, headers=csrf_headers(client)
    )

    page = (await client.get("/api/library", params={"rating_gte": 4})).json()

    assert [item["title"] for item in page["items"]] == ["Good"]
    assert page["items"][0]["rating"] == 5.0
    assert page["items"][0]["rating_count"] == 1


async def test_the_detail_reports_ownership_and_the_aggregate_rating(
    app: FastAPI, client: AsyncClient
) -> None:
    alice = await create_user(app, username="alice")
    media = await _media(app, "Owned", owner=alice)
    await login(client, "alice", PASSWORD)
    await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 4}, headers=csrf_headers(client)
    )

    detail = (await client.get(f"/api/media/{media.id}")).json()

    assert detail["owner_id"] == str(alice.id)
    assert detail["in_my_library"] is True
    assert (detail["rating"], detail["rating_count"]) == (4.0, 1)


async def test_a_comment_never_carries_a_name_even_when_one_is_set(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    media = await _media(app, "Aurora")
    await login(client, "alice", PASSWORD)
    await client.patch(
        "/api/account/profile", json={"display_name": "Mira"}, headers=csrf_headers(client)
    )
    await client.post(
        f"/api/media/{media.id}/comments", json={"body": "Good one."}, headers=csrf_headers(client)
    )

    body = (await client.get(f"/api/media/{media.id}/comments")).json()[0]

    # A frontend cannot leak a name it was never sent.
    assert body["author"] is None
    assert body["is_own"] is True


async def test_attribution_can_be_turned_on_deliberately(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app, username="alice")
    media = await _media(app, "Aurora")
    await _set_flag(app, "anonymous_social", False)
    await login(client, "alice", PASSWORD)
    await client.patch(
        "/api/account/profile", json={"display_name": "Mira"}, headers=csrf_headers(client)
    )
    await client.post(
        f"/api/media/{media.id}/comments", json={"body": "Good one."}, headers=csrf_headers(client)
    )

    body = (await client.get(f"/api/media/{media.id}/comments")).json()[0]

    assert body["author"] == "Mira"


async def test_an_administrator_can_hide_and_resolve_and_filter_by_state(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    media = await _media(app, "Aurora")
    await login(client, "alice", PASSWORD)
    comment_id = (
        await client.post(
            f"/api/media/{media.id}/comments",
            json={"body": "Audio is out of sync."},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    hidden = await client.post(
        f"/api/admin/comments/{comment_id}/hide", headers=csrf_headers(client)
    )
    only_hidden = await client.get("/api/admin/comments", params={"state": "hidden"})
    only_open = await client.get("/api/admin/comments", params={"state": "open"})
    resolved = await client.post(
        f"/api/admin/comments/{comment_id}/resolve", headers=csrf_headers(client)
    )

    assert hidden.json()["state"] == "hidden"
    assert [row["id"] for row in only_hidden.json()] == [comment_id]
    assert only_open.json() == []
    assert resolved.json()["state"] == "answered"
    # The moderation queue is the one place a name is useful: the administrator
    # deciding on a report needs to know whether one person wrote all six.
    assert resolved.json()["author"] == "alice"


async def test_the_watchlist_is_a_private_queue(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app, username="alice")
    await create_user(app, username="bob")
    media = await _media(app, "Aurora")

    await login(client, "alice", PASSWORD)
    added = await client.post(
        "/api/watchlist", json={"media_id": str(media.id)}, headers=csrf_headers(client)
    )
    twice = await client.post(
        "/api/watchlist", json={"media_id": str(media.id)}, headers=csrf_headers(client)
    )
    mine = await client.get("/api/watchlist")

    await login(client, "bob", PASSWORD)
    theirs = await client.get("/api/watchlist")

    await login(client, "alice", PASSWORD)
    removed = await client.delete(f"/api/watchlist/{media.id}", headers=csrf_headers(client))
    gone = await client.delete(f"/api/watchlist/{media.id}", headers=csrf_headers(client))

    assert (added.status_code, twice.status_code) == (204, 204)
    assert [item["title"] for item in mine.json()] == ["Aurora"]
    assert theirs.json() == []
    assert (removed.status_code, gone.status_code) == (204, 404)


async def test_progress_remembers_the_device_it_came_from(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    media = await _media(app, "Aurora")
    await login(client, "alice", PASSWORD)

    await client.post(
        f"/api/playback/{media.id}/progress",
        json={"position_seconds": 10, "duration_seconds": 600, "device_label": "Living room TV"},
        headers=csrf_headers(client),
    )
    # A client that does not name itself must not blank the label.
    await client.post(
        f"/api/playback/{media.id}/progress",
        json={"position_seconds": 20, "duration_seconds": 600},
        headers=csrf_headers(client),
    )
    resuming = (await client.get("/api/playback/continue-watching")).json()

    assert resuming[0]["device_label"] == "Living room TV"


async def test_playing_on_is_empty_when_nothing_is_transcoding(
    app: FastAPI, client: AsyncClient
) -> None:
    """Direct play keeps no session, so no row is the signal that it is direct."""

    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)

    response = await client.get("/api/playback/sessions/mine")

    assert response.status_code == 200
    assert response.json() == []


async def test_markers_can_be_added_edited_and_removed_by_an_administrator(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    await create_user(app, username="root", role=UserRole.ADMIN)
    media = await _media(app, "Aurora")

    await login(client, "alice", PASSWORD)
    forbidden = await client.post(
        f"/api/media/{media.id}/markers",
        json={"start_seconds": 10, "end_seconds": 40},
        headers=csrf_headers(client),
    )

    await login(client, "root", PASSWORD)
    created = await client.post(
        f"/api/media/{media.id}/markers",
        json={"start_seconds": 10, "end_seconds": 40},
        headers=csrf_headers(client),
    )
    marker_id = created.json()["id"]
    edited = await client.patch(
        f"/api/media/{media.id}/markers/{marker_id}",
        json={"end_seconds": 50},
        headers=csrf_headers(client),
    )
    backwards = await client.patch(
        f"/api/media/{media.id}/markers/{marker_id}",
        json={"end_seconds": 5},
        headers=csrf_headers(client),
    )
    removed = await client.delete(
        f"/api/media/{media.id}/markers/{marker_id}", headers=csrf_headers(client)
    )

    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert edited.json()["end_seconds"] == 50
    assert backwards.status_code == 422
    assert removed.status_code == 204


async def test_shorts_are_cut_from_the_markers_and_never_duplicated(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    media = await _media(app, "Aurora")
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media_file = await session.scalar(select(MediaFile).where(MediaFile.media_id == media.id))
        assert media_file is not None
        session.add_all(
            SceneMarker(
                media_file_id=media_file.id,
                ordinal=index,
                start_seconds=index * 120.0,
                end_seconds=index * 120.0 + 30,
            )
            for index in range(3)
        )
        await session.commit()

    await login(client, "root", PASSWORD)
    generated = await client.post(
        f"/api/shorts/media/{media.id}/generate",
        json={"maximum": 2, "seconds": 60},
        headers=csrf_headers(client),
    )
    again = await client.post(
        f"/api/shorts/media/{media.id}/generate",
        json={"maximum": 2, "seconds": 60},
        headers=csrf_headers(client),
    )

    assert generated.status_code == 201
    clips = generated.json()
    assert len(clips) == 2
    # A marker is a boundary, not a length: the clip stops at the scene end.
    assert clips[0]["duration_seconds"] == 30
    assert clips[0]["parent_media_id"] == str(media.id)
    assert clips[0]["marker_id"] is not None
    # `maximum` bounds one call, not the title: pressing again picks up at the
    # first marker that has no clip yet, and never re-cuts one that has.
    assert [clip["marker_id"] for clip in again.json()] not in ([], None)
    assert len(again.json()) == 1
    third = await client.post(
        f"/api/shorts/media/{media.id}/generate",
        json={"maximum": 2, "seconds": 60},
        headers=csrf_headers(client),
    )
    assert third.json() == []


async def test_generating_without_markers_says_so(app: FastAPI, client: AsyncClient) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    media = await _media(app, "Aurora")
    await login(client, "root", PASSWORD)

    response = await client.post(
        f"/api/shorts/media/{media.id}/generate", json={}, headers=csrf_headers(client)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SHORTS_NO_MARKERS"


async def test_the_shorts_feed_accepts_every_documented_sort(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="alice")
    await login(client, "alice", PASSWORD)

    codes = [
        (await client.get("/api/shorts", params={"sort": sort})).status_code
        for sort in ("trending", "newest", "top", "duration")
    ]
    rejected = await client.get("/api/shorts", params={"sort": "sideways"})

    assert codes == [200, 200, 200, 200]
    assert rejected.status_code == 422


async def test_a_guest_cannot_aim_an_import_at_someone_else(
    app: FastAPI, client: AsyncClient
) -> None:
    from pornarr_api.routers.requests import resolve_target_owner

    alice = await create_user(app, username="alice")
    bob = await create_user(app, username="bob")
    root = await create_user(app, username="root", role=UserRole.ADMIN)

    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        own = await resolve_target_owner(session, alice, None)
        explicit_own = await resolve_target_owner(session, alice, alice.id)
        by_admin = await resolve_target_owner(session, root, bob.id)
        try:
            await resolve_target_owner(session, alice, bob.id)
        except Exception as error:
            refused = getattr(error, "status_code", None)
        else:  # pragma: no cover - reached only if the guard is removed
            refused = None

    assert own == alice.id
    assert explicit_own == alice.id
    assert by_admin == bob.id
    assert refused == 403


async def test_a_recommendation_carries_a_match_score_and_readable_reasons(
    app: FastAPI, client: AsyncClient
) -> None:
    from datetime import UTC, datetime, timedelta

    from pornarr_db.models.recommendation import RecommendationCandidate

    alice = await create_user(app, username="alice")
    media = await _media(app, "Aurora")
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        session.add(
            RecommendationCandidate(
                user_id=alice.id,
                media_id=media.id,
                score=0.94,
                # The shape the recommender stores: the weights sit one level
                # down, next to the matched tags, performers and studios.
                reason_json={
                    "dominant_factor": "tag",
                    "score_breakdown": {"tag": 0.6, "studio": 0.34, "recency": 0.0},
                },
                model_version="test",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()

    await login(client, "alice", PASSWORD)
    entry = (await client.get("/api/recommendations")).json()[0]

    assert entry["match_score"] == 94
    # Strongest first, and a signal that contributed nothing explains nothing.
    assert entry["reasons"] == ["Tags you keep watching", "A studio you finish"]


async def test_sent_to_me_is_the_one_place_a_name_appears(
    app: FastAPI, client: AsyncClient
) -> None:
    alice = await create_user(app, username="alice")
    bob = await create_user(app, username="bob")
    media = await _media(app, "Aurora")

    await login(client, "alice", PASSWORD)
    await client.patch(
        "/api/account/profile", json={"display_name": "Mira"}, headers=csrf_headers(client)
    )
    sent = await client.post(
        "/api/recommendations/send",
        json={"recipient_id": str(bob.id), "media_id": str(media.id), "note": "Watch this."},
        headers=csrf_headers(client),
    )

    await login(client, "bob", PASSWORD)
    inbox = await client.get("/api/recommendations/sent-to-me")

    assert sent.status_code == 201
    assert inbox.json()[0]["sender"] == "Mira"
    assert inbox.json()[0]["note"] == "Watch this."
    assert alice.id != bob.id


async def test_an_unknown_title_still_404s_across_the_new_endpoints(
    app: FastAPI, client: AsyncClient
) -> None:
    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    missing = uuid4()

    codes = [
        (await client.get(f"/api/media/{missing}/scenes")).status_code,
        (
            await client.post(
                "/api/watchlist", json={"media_id": str(missing)}, headers=csrf_headers(client)
            )
        ).status_code,
        (
            await client.post(
                f"/api/media/{missing}/markers",
                json={"start_seconds": 0, "end_seconds": 10},
                headers=csrf_headers(client),
            )
        ).status_code,
        (
            await client.post(
                f"/api/shorts/media/{missing}/generate", json={}, headers=csrf_headers(client)
            )
        ).status_code,
    ]

    assert codes == [404, 404, 404, 404]
