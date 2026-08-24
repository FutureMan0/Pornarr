"""Ratings, comments, shorts, sends, collections and the display name.

The tests that matter here are the boundary ones: a private collection must not
be readable by anyone but its owner, a report must not tell the reporter
anything, and an administrator must be able to hide a comment without being
able to rewrite it.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.media import Media
from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)

PASSWORD = "correct horse battery staple"


async def _media(app: FastAPI, title: str = "Aurora 214") -> Media:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        media = Media(title=title, normalized_title=title.casefold())
        session.add(media)
        await session.commit()
    return media


async def _signed_in(
    app: FastAPI, client: AsyncClient, username: str, role: UserRole = UserRole.USER
):
    user = await create_user(app, username=username, role=role)
    await login(client, username, PASSWORD)
    return user


async def test_rating_is_one_per_person_and_summarised_with_a_breakdown(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)

    first = await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 4}, headers=csrf_headers(client)
    )
    second = await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 5}, headers=csrf_headers(client)
    )

    assert first.status_code == 200
    assert second.status_code == 200
    body = second.json()
    # Re-rating replaces rather than accumulates.
    assert body["count"] == 1
    assert body["average"] == 5.0
    assert body["your_stars"] == 5
    assert body["breakdown"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 1}


async def test_clearing_a_rating_is_idempotent_and_out_of_range_is_rejected(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)
    await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 3}, headers=csrf_headers(client)
    )

    cleared = await client.delete(f"/api/media/{media.id}/rating", headers=csrf_headers(client))
    again = await client.delete(f"/api/media/{media.id}/rating", headers=csrf_headers(client))
    invalid = await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 6}, headers=csrf_headers(client)
    )

    assert cleared.status_code == 200
    assert cleared.json()["your_stars"] is None
    assert again.status_code == 200
    assert again.json()["count"] == 0
    assert invalid.status_code == 422


async def test_a_missing_title_is_404_rather_than_an_orphan_rating(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")

    response = await client.put(
        f"/api/media/{uuid4()}/rating", json={"stars": 3}, headers=csrf_headers(client)
    )

    assert response.status_code == 404


async def test_a_comment_is_anonymous_by_default_but_carries_the_stars(
    app: FastAPI, client: AsyncClient
) -> None:
    """Anonymity is enforced here, not in the client: no name is ever sent."""

    await _signed_in(app, client, "alice")
    media = await _media(app)
    await client.patch(
        "/api/account/profile", json={"display_name": "Mira"}, headers=csrf_headers(client)
    )
    await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 4}, headers=csrf_headers(client)
    )

    created = await client.post(
        f"/api/media/{media.id}/comments",
        json={"body": "  Looks great on the TV.  "},
        headers=csrf_headers(client),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["author"] is None
    assert body["body"] == "Looks great on the TV."
    assert body["stars"] == 4
    assert body["is_own"] is True
    assert body["likes"] == 0


async def test_a_blank_comment_is_rejected(app: FastAPI, client: AsyncClient) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)

    response = await client.post(
        f"/api/media/{media.id}/comments", json={"body": "   "}, headers=csrf_headers(client)
    )

    assert response.status_code == 422


async def test_liking_twice_counts_once_and_unliking_reverses_it(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)
    comment_id = (
        await client.post(
            f"/api/media/{media.id}/comments",
            json={"body": "Sound mix is the best of the run."},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await client.put(f"/api/comments/{comment_id}/like", headers=csrf_headers(client))
    await client.put(f"/api/comments/{comment_id}/like", headers=csrf_headers(client))
    after_likes = (await client.get(f"/api/media/{media.id}/comments")).json()[0]
    await client.delete(f"/api/comments/{comment_id}/like", headers=csrf_headers(client))
    after_unlike = (await client.get(f"/api/media/{media.id}/comments")).json()[0]

    assert after_likes["likes"] == 1
    assert after_likes["you_liked"] is True
    assert after_unlike["likes"] == 0
    assert after_unlike["you_liked"] is False


async def test_only_the_author_edits_but_an_administrator_can_delete(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)
    comment_id = (
        await client.post(
            f"/api/media/{media.id}/comments",
            json={"body": "Second half drags a little."},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    stranger_edit = await client.patch(
        f"/api/comments/{comment_id}", json={"body": "rewritten"}, headers=csrf_headers(client)
    )
    stranger_delete = await client.delete(
        f"/api/comments/{comment_id}", headers=csrf_headers(client)
    )

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    admin_edit = await client.patch(
        f"/api/comments/{comment_id}", json={"body": "rewritten"}, headers=csrf_headers(client)
    )
    admin_delete = await client.delete(f"/api/comments/{comment_id}", headers=csrf_headers(client))

    assert stranger_edit.status_code == 403
    assert stranger_delete.status_code == 403
    # Moderation is remove-or-hide. Rewriting someone's words is not moderation.
    assert admin_edit.status_code == 403
    assert admin_delete.status_code == 204


async def test_a_report_tells_the_reporter_nothing_and_reaches_only_the_admin_queue(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)
    comment_id = (
        await client.post(
            f"/api/media/{media.id}/comments",
            json={"body": "Wrong performer tagged here I think."},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    reported = await client.post(
        f"/api/comments/{comment_id}/report",
        json={"reason": "wrong tag"},
        headers=csrf_headers(client),
    )
    twice = await client.post(
        f"/api/comments/{comment_id}/report",
        json={"reason": "still wrong"},
        headers=csrf_headers(client),
    )
    guest_view = await client.get(f"/api/media/{media.id}/comments")
    guest_queue = await client.get("/api/admin/comments")

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    queue = await client.get("/api/admin/comments", params={"reported_only": True})

    assert reported.status_code == 202
    assert twice.status_code == 202
    # No report count leaks into the reader-facing shape at all.
    assert "reports" not in guest_view.json()[0]
    assert guest_queue.status_code == 403
    assert queue.status_code == 200
    # Reporting twice from one account is one report, not two.
    assert [(row["id"], row["reports"]) for row in queue.json()] == [(comment_id, 1)]


async def test_hiding_a_comment_withholds_it_from_others_but_not_its_author(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")
    media = await _media(app)
    comment_id = (
        await client.post(
            f"/api/media/{media.id}/comments",
            json={"body": "Audio is out of sync from the start."},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    hidden = await client.patch(
        f"/api/admin/comments/{comment_id}", json={"state": "hidden"}, headers=csrf_headers(client)
    )

    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    stranger = await client.get(f"/api/media/{media.id}/comments")

    await login(client, "alice", PASSWORD)
    author = await client.get(f"/api/media/{media.id}/comments")

    assert hidden.status_code == 200
    assert hidden.json()["media_title"] == "Aurora 214"
    assert stranger.json() == []
    assert [row["state"] for row in author.json()] == ["hidden"]


async def test_a_short_must_stay_within_the_clip_length_and_only_an_admin_creates_one(
    app: FastAPI, client: AsyncClient
) -> None:
    media = await _media(app)
    await _signed_in(app, client, "alice")
    guest_attempt = await client.post(
        "/api/admin/shorts",
        json={
            "media_id": str(media.id),
            "title": "the good bit",
            "start_seconds": 10,
            "end_seconds": 40,
        },
        headers=csrf_headers(client),
    )

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    too_long = await client.post(
        "/api/admin/shorts",
        json={
            "media_id": str(media.id),
            "title": "whole scene",
            "start_seconds": 0,
            "end_seconds": 301,
        },
        headers=csrf_headers(client),
    )
    created = await client.post(
        "/api/admin/shorts",
        json={
            "media_id": str(media.id),
            "title": "the good bit",
            "start_seconds": 10,
            "end_seconds": 52,
        },
        headers=csrf_headers(client),
    )
    duplicate = await client.post(
        "/api/admin/shorts",
        json={
            "media_id": str(media.id),
            "title": "same offset",
            "start_seconds": 10,
            "end_seconds": 30,
        },
        headers=csrf_headers(client),
    )

    assert guest_attempt.status_code == 403
    assert too_long.status_code == 422
    assert created.status_code == 201
    assert created.json()["duration_seconds"] == 42
    assert created.json()["media_title"] == "Aurora 214"
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "SHORT_ALREADY_EXISTS"


async def test_the_shorts_feed_carries_the_rail_counters(app: FastAPI, client: AsyncClient) -> None:
    media = await _media(app)
    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    await client.post(
        "/api/admin/shorts",
        json={
            "media_id": str(media.id),
            "title": "opener",
            "start_seconds": 0,
            "end_seconds": 55,
        },
        headers=csrf_headers(client),
    )
    await client.put(
        f"/api/media/{media.id}/rating", json={"stars": 5}, headers=csrf_headers(client)
    )
    await client.post(
        f"/api/media/{media.id}/comments",
        json={"body": "Best in the library."},
        headers=csrf_headers(client),
    )

    feed = await client.get("/api/shorts")

    assert feed.status_code == 200
    assert [(row["average_stars"], row["comment_count"]) for row in feed.json()] == [(5.0, 1)]


async def test_a_send_reaches_the_recipient_once_and_never_yourself(
    app: FastAPI, client: AsyncClient
) -> None:
    media = await _media(app)
    alice = await _signed_in(app, client, "alice")
    bob = await create_user(app, username="bob")
    await client.patch(
        "/api/account/profile", json={"display_name": "Mira"}, headers=csrf_headers(client)
    )

    to_self = await client.post(
        "/api/sends",
        json={"recipient_id": str(alice.id), "media_id": str(media.id)},
        headers=csrf_headers(client),
    )
    sent = await client.post(
        "/api/sends",
        json={
            "recipient_id": str(bob.id),
            "media_id": str(media.id),
            "note": "Watch this before we talk about it.",
        },
        headers=csrf_headers(client),
    )
    twice = await client.post(
        "/api/sends",
        json={"recipient_id": str(bob.id), "media_id": str(media.id)},
        headers=csrf_headers(client),
    )

    await login(client, "bob", PASSWORD)
    inbox = await client.get("/api/sends/received")

    assert to_self.status_code == 422
    assert to_self.json()["code"] == "SEND_TO_SELF"
    assert sent.status_code == 201
    assert twice.status_code == 409
    row = inbox.json()[0]
    # The recipient sees the sender's chosen name, not their username.
    assert row["sender"] == "Mira"
    assert row["note"] == "Watch this before we talk about it."
    assert row["seen_at"] is None


async def test_the_sender_gets_no_read_receipt(app: FastAPI, client: AsyncClient) -> None:
    media = await _media(app)
    await _signed_in(app, client, "alice")
    bob = await create_user(app, username="bob")
    send_id = (
        await client.post(
            "/api/sends",
            json={"recipient_id": str(bob.id), "media_id": str(media.id)},
            headers=csrf_headers(client),
        )
    ).json()["id"]

    await login(client, "bob", PASSWORD)
    seen = await client.post(f"/api/sends/{send_id}/seen", headers=csrf_headers(client))
    recipient_view = await client.get("/api/sends/received")

    await login(client, "alice", PASSWORD)
    outbox = await client.get("/api/sends/sent")

    assert seen.status_code == 200
    assert recipient_view.json()[0]["seen_at"] is not None
    # The sender learns that it was delivered, never that it was opened.
    assert outbox.json()[0]["seen_at"] is None


async def test_a_private_collection_is_invisible_to_everyone_else(
    app: FastAPI, client: AsyncClient
) -> None:
    media = await _media(app)
    await _signed_in(app, client, "alice")
    collection_id = (
        await client.post(
            "/api/collections", json={"name": "Late shift"}, headers=csrf_headers(client)
        )
    ).json()["id"]
    await client.put(
        f"/api/collections/{collection_id}/items/{media.id}", headers=csrf_headers(client)
    )

    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    stranger_read = await client.get(f"/api/collections/{collection_id}")
    stranger_list = await client.get("/api/collections", params={"shared": True})

    await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    admin_read = await client.get(f"/api/collections/{collection_id}")

    # 404 rather than 403: a 403 would confirm the shelf exists.
    assert stranger_read.status_code == 404
    assert stranger_list.json() == []
    # Administration covers the library, not what a guest saved.
    assert admin_read.status_code == 404


async def test_sharing_a_collection_opens_exactly_that_one(
    app: FastAPI, client: AsyncClient
) -> None:
    media = await _media(app)
    await _signed_in(app, client, "alice")
    shared_id = (
        await client.post(
            "/api/collections",
            json={"name": "Shared picks", "visibility": "shared"},
            headers=csrf_headers(client),
        )
    ).json()["id"]
    private_id = (
        await client.post(
            "/api/collections", json={"name": "Private"}, headers=csrf_headers(client)
        )
    ).json()["id"]
    await client.put(f"/api/collections/{shared_id}/items/{media.id}", headers=csrf_headers(client))

    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    visible = await client.get("/api/collections", params={"shared": True})
    detail = await client.get(f"/api/collections/{shared_id}")
    still_hidden = await client.get(f"/api/collections/{private_id}")
    cannot_add = await client.put(
        f"/api/collections/{shared_id}/items/{media.id}", headers=csrf_headers(client)
    )

    assert [row["id"] for row in visible.json()] == [shared_id]
    assert detail.json()["items"][0]["media_title"] == "Aurora 214"
    assert detail.json()["is_yours"] is False
    assert still_hidden.status_code == 404
    # Shared means readable, not writable.
    assert cannot_add.status_code == 404


async def test_collection_names_are_unique_per_owner_and_items_are_idempotent(
    app: FastAPI, client: AsyncClient
) -> None:
    media = await _media(app)
    await _signed_in(app, client, "alice")
    first = await client.post(
        "/api/collections", json={"name": "Late shift"}, headers=csrf_headers(client)
    )
    duplicate = await client.post(
        "/api/collections", json={"name": "Late shift"}, headers=csrf_headers(client)
    )
    collection_id = first.json()["id"]
    await client.put(
        f"/api/collections/{collection_id}/items/{media.id}", headers=csrf_headers(client)
    )
    await client.put(
        f"/api/collections/{collection_id}/items/{media.id}", headers=csrf_headers(client)
    )
    detail = await client.get(f"/api/collections/{collection_id}")

    # A second owner may reuse the name; uniqueness is per person, not global.
    await create_user(app, username="bob")
    await login(client, "bob", PASSWORD)
    other_owner = await client.post(
        "/api/collections", json={"name": "Late shift"}, headers=csrf_headers(client)
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "COLLECTION_ALREADY_EXISTS"
    assert detail.json()["item_count"] == 1
    assert other_owner.status_code == 201


async def test_the_display_name_is_separate_from_the_credential_and_can_be_cleared(
    app: FastAPI, client: AsyncClient
) -> None:
    await _signed_in(app, client, "alice")

    default = await client.get("/api/account/profile")
    named = await client.patch(
        "/api/account/profile", json={"display_name": "  Mira  "}, headers=csrf_headers(client)
    )
    blanked = await client.patch(
        "/api/account/profile", json={"display_name": "   "}, headers=csrf_headers(client)
    )
    still_signs_in = await client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}
    )

    assert default.json()["author_name"] == "alice"
    assert named.json() == {
        "username": "alice",
        "display_name": "Mira",
        "author_name": "Mira",
        "role": "user",
    }
    # Whitespace is not a name: clearing falls back rather than leaving it blank.
    assert blanked.json()["display_name"] is None
    assert blanked.json()["author_name"] == "alice"
    # Changing how you appear never changes how you sign in.
    assert still_signs_in.status_code == 200
