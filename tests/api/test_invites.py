"""Inviting somebody onto this server.

Two of these endpoints are reachable without an account, so most of this file is
about what they refuse: a second account from one link, an administrator role
from a copied URL, and any detail about a token the caller does not hold.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pornarr_api.main import create_app
from pornarr_api.routers.invites import token_hash
from pornarr_db.base import Base
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.invite import Invite
from pornarr_db.models.user import User, UserRole
from tests.api.test_app import build_settings
from tests.api.test_auth import MemoryRedis, create_user, csrf_headers, login

PASSWORD = "correct horse battery staple"
GUEST_PASSWORD = "another perfectly fine passphrase"


@pytest.fixture
async def app() -> AsyncIterator:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(build_settings())
    application.state.engine, application.state.redis = engine, MemoryRedis()
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


def factory(app):
    return async_sessionmaker(app.state.engine, class_=AsyncSession, expire_on_commit=False)


async def _admin(app, client: AsyncClient) -> User:
    user = await create_user(app, username="root", role=UserRole.ADMIN)
    await login(client, "root", PASSWORD)
    return user


async def _invite(app, client: AsyncClient, **body: object) -> dict:
    response = await client.post("/api/admin/invites", json=body, headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_token_is_returned_once_and_never_stored(app, client: AsyncClient) -> None:
    await _admin(app, client)

    created = await _invite(app, client, valid_days=7, note="for Lea")

    async with factory(app)() as session:
        stored = (await session.scalars(select(Invite))).one()
        # A database dump, a backup or a stray log line must not be enough to
        # join a household.
        assert stored.token_hash == token_hash(created["token"])
        assert created["token"] not in stored.token_hash

    listed = (await client.get("/api/admin/invites")).json()
    assert "token" not in listed[0]


async def test_redeeming_creates_a_guest_and_spends_the_link(app, client: AsyncClient) -> None:
    admin = await _admin(app, client)
    created = await _invite(app, client)

    response = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD, "display_name": "Mira"},
    )

    assert response.status_code == 201
    assert response.json() == {"username": "mira", "display_name": "Mira", "role": "user"}

    async with factory(app)() as session:
        guest = await session.scalar(select(User).where(User.username == "mira"))
        assert guest is not None
        assert guest.id != admin.id
        # Every account gets its automation policy; a guest without one would
        # fall over the first time anything consulted it.
        assert await session.get(AutomationRule, guest.id) is not None
        invite = (await session.scalars(select(Invite))).one()
        assert invite.redeemed_by == guest.id
        assert invite.redeemed_at is not None


async def test_a_link_works_once(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client)

    first = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD},
    )
    second = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "tobi", "password": GUEST_PASSWORD},
    )

    assert first.status_code == 201
    assert second.status_code == 404


async def test_an_invitation_cannot_mint_an_administrator(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client)

    # Even asked for outright: the field is not part of the payload, and the
    # role is decided by the server.
    response = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD, "role": "admin"},
    )

    assert response.status_code == 201
    assert response.json()["role"] == "user"


async def test_an_expired_link_is_refused(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client)
    async with factory(app)() as session:
        invite = (await session.scalars(select(Invite))).one()
        invite.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(invite)
        await session.commit()

    state = await client.get(f"/api/invites/{created['token']}")
    redeemed = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD},
    )

    assert state.json() == {"valid": False, "expires_at": None}
    assert redeemed.status_code == 404


async def test_a_link_that_never_existed_looks_the_same_as_a_spent_one(
    app, client: AsyncClient
) -> None:
    await _admin(app, client)
    created = await _invite(app, client)
    await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD},
    )

    spent = await client.get(f"/api/invites/{created['token']}")
    nonsense = await client.get("/api/invites/not-a-real-token")

    # Which of the two it is tells a stranger something about links they do not
    # hold.
    assert spent.json() == nonsense.json() == {"valid": False, "expires_at": None}


async def test_a_valid_link_discloses_only_that_it_is_valid(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client, note="for Lea's iPad")

    body = (await client.get(f"/api/invites/{created['token']}")).json()

    assert body["valid"] is True
    assert body["expires_at"] is not None
    # Not the note, not who issued it, not the household's name. Whoever holds
    # the URL might have found it in a browser history on a shared machine.
    assert set(body) == {"valid", "expires_at"}


async def test_a_taken_name_is_refused_without_spending_the_link(app, client: AsyncClient) -> None:
    await _admin(app, client)
    await create_user(app, username="mira", role=UserRole.USER)
    created = await _invite(app, client)

    response = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD},
    )

    assert response.status_code == 409
    async with factory(app)() as session:
        invite = (await session.scalars(select(Invite))).one()
        # The link must survive a typo. Spending it here would mean issuing a
        # fresh one for every mistyped name.
        assert invite.redeemed_at is None


async def test_a_short_password_is_refused(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client)

    response = await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": "short"},
    )

    assert response.status_code == 422


async def test_only_an_administrator_issues_invitations(app, client: AsyncClient) -> None:
    await create_user(app, username="mira", role=UserRole.USER)
    await login(client, "mira", PASSWORD)

    created = await client.post("/api/admin/invites", json={}, headers=csrf_headers(client))
    listed = await client.get("/api/admin/invites")

    # An invitation is a way onto the server; a guest handing them out would
    # make the household's membership everyone's decision.
    assert created.status_code == 403
    assert listed.status_code == 403


async def test_an_unused_invitation_can_be_withdrawn(app, client: AsyncClient) -> None:
    await _admin(app, client)
    created = await _invite(app, client)

    revoked = await client.delete(
        f"/api/admin/invites/{created['id']}", headers=csrf_headers(client)
    )
    state = await client.get(f"/api/invites/{created['token']}")

    assert revoked.status_code == 204
    assert state.json()["valid"] is False


async def test_a_used_invitation_is_kept_as_the_record_of_who_joined(
    app, client: AsyncClient
) -> None:
    await _admin(app, client)
    created = await _invite(app, client)
    await client.post(
        f"/api/invites/{created['token']}/redeem",
        json={"username": "mira", "password": GUEST_PASSWORD},
    )

    revoked = await client.delete(
        f"/api/admin/invites/{created['id']}", headers=csrf_headers(client)
    )
    listed = (await client.get("/api/admin/invites")).json()

    # Deleting it would lose the fact somebody would want later: who let this
    # person in, and through which link.
    assert revoked.status_code == 409
    assert listed[0]["redeemed_username"] == "mira"
