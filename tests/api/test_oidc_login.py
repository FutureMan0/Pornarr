"""OIDC authorization-code login flow."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.oidc import OidcAuthenticationError, validate_id_token
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
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


async def _provider(app, **settings: object) -> OidcProvider:
    # A dict merge rather than a second set of keyword arguments, so a caller
    # distinguishing two providers -- this file's own anonymous-listing tests
    # need a visible one and a disabled one -- can override `name` or `issuer`
    # without colliding with the defaults below.
    defaults: dict[str, object] = {
        "name": "example",
        "issuer": "https://issuer.example",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["openid", "profile"],
        "discovery_document": {
            "issuer": "https://issuer.example",
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        },
    }
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        provider = OidcProvider(**{**defaults, **settings})
        session.add(provider)
        await session.commit()
    return provider


async def test_oidc_login_redirects_with_pkce_state_and_nonce(app, client) -> None:
    await create_user(app)
    provider = await _provider(app)

    response = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)

    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-id"]
    assert query["scope"] == ["openid profile"]
    assert query["code_challenge_method"] == ["S256"]
    state = query["state"][0]
    state_record = json.loads(app.state.redis.values[f"pornarr:auth:oidc:{state}"])
    verifier = state_record["code_verifier"]
    expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert query["code_challenge"] == [expected_challenge.rstrip(b"=").decode()]
    assert state_record["nonce"] == query["nonce"][0]


async def test_anonymous_login_screen_can_list_enabled_providers(app, client) -> None:
    """`/login` has to offer a button before anybody has a session, so the
    list has to be reachable by somebody who is not signed in.
    """
    # An account has to exist for `SetupMiddleware` to serve anything at all --
    # a fact about a fresh install, not about this endpoint -- and the request
    # below carries no session, which is the property under test.
    await create_user(app)
    visible = await _provider(app, name="Visible provider")
    await _provider(
        app,
        name="Hidden provider",
        issuer="https://issuer.example/other",
        enabled=False,
    )

    response = await client.get("/api/auth/oidc/providers")

    assert response.status_code == 200
    assert response.json() == [{"id": str(visible.id), "name": "Visible provider"}]


async def test_provider_list_discloses_nothing_but_id_and_name(app, client) -> None:
    await create_user(app)
    await _provider(app, name="Visible provider")

    response = await client.get("/api/auth/oidc/providers")

    assert set(response.json()[0].keys()) == {"id", "name"}
    # Nothing from `ProviderResponse` -- issuer, client id or discovery state --
    # leaks into a document a stranger without an account can read.
    assert "issuer.example" not in response.text
    assert "client-id" not in response.text
    assert "client-secret" not in response.text


async def test_oidc_callback_creates_the_normal_session_and_consumes_state(
    app, client, monkeypatch
) -> None:
    user = await create_user(app)
    provider = await _provider(app)
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        session.add(OidcIdentity(provider_id=provider.id, user_id=user.id, subject="provider-user"))
        await session.commit()

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, str]:
        return {"sub": "provider-user"}

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert (await client.get("/api/auth/me")).json()["id"] == str(user.id)
    replay = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert replay.json()["code"] == "OIDC_STATE_INVALID"


async def test_oidc_jit_provisions_and_re_evaluates_the_role(app, client, monkeypatch) -> None:
    await create_user(app)
    provider = await _provider(
        app,
        default_role=UserRole.USER,
        username_claim="preferred_username",
        role_claim="groups",
        role_mapping={"administrators": UserRole.ADMIN},
    )
    claims: dict[str, object] = {
        "sub": "provider-user",
        "preferred_username": "oidc-user",
        "groups": ["viewer"],
    }

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, object]:
        return claims

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)

    async def callback() -> None:
        started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        response = await client.get(
            "/api/auth/oidc/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 303

    await callback()
    current = (await client.get("/api/auth/me")).json()
    assert current["username"] == "oidc-user"
    assert current["role"] == "user"
    local_login = await client.post(
        "/api/auth/login", json={"username": "oidc-user", "password": "not-the-random-password"}
    )
    assert local_login.status_code == 401

    claims["groups"] = ["administrators"]
    await callback()

    assert (await client.get("/api/auth/me")).json()["role"] == "admin"


async def test_oidc_refuses_an_identity_outside_the_provider_allowlist(
    app, client, monkeypatch
) -> None:
    await create_user(app)
    provider = await _provider(app, required_claim="tenant", required_claim_value="trusted")

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, str]:
        return {"sub": "provider-user", "preferred_username": "oidc-user", "tenant": "other"}

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    response = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "OIDC_IDENTITY_NOT_ALLOWED"


async def test_oidc_links_to_the_signed_in_account_and_survives_logout(
    app, client, monkeypatch
) -> None:
    user = await create_user(app)
    provider = await _provider(app)
    await login(client, user.username, "correct horse battery staple")

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, str]:
        return {"sub": "provider-user"}

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/link", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 303
    linked = (await client.get("/api/account/oidc")).json()
    assert len(linked) == 1
    assert linked[0]["provider_id"] == str(provider.id)
    assert linked[0]["provider_name"] == "example"
    assert (await client.post("/api/auth/logout", headers=csrf_headers(client))).status_code == 204
    await login(client, user.username, "correct horse battery staple")
    assert (await client.get("/api/account/oidc")).json() == linked
    assert (
        await client.delete(f"/api/account/oidc/{linked[0]['id']}", headers=csrf_headers(client))
    ).status_code == 204
    assert (await client.get("/api/account/oidc")).json() == []


async def test_oidc_link_callback_requires_the_initiating_session(app, client, monkeypatch) -> None:
    user = await create_user(app)
    provider = await _provider(app)
    await login(client, user.username, "correct horse battery staple")

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, str]:
        return {"sub": "provider-user"}

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/link", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    client.cookies.clear()

    response = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "OIDC_STATE_INVALID"


async def test_oidc_auto_links_a_verified_email(app, client, monkeypatch) -> None:
    user = await create_user(app, username="local@example.test")
    provider = await _provider(app)

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, object]:
        return {
            "sub": "provider-user",
            "preferred_username": "other-name",
            "email": user.username,
            "email_verified": True,
        }

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    assert (
        await client.get(
            "/api/auth/oidc/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
    ).status_code == 303
    assert (await client.get("/api/auth/me")).json()["id"] == str(user.id)


async def test_oidc_does_not_auto_link_an_unverified_email(app, client, monkeypatch) -> None:
    user = await create_user(app, username="local@example.test")
    provider = await _provider(app)

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, object]:
        return {
            "sub": "provider-user",
            "preferred_username": "other-name",
            "email": user.username,
            "email_verified": False,
        }

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    assert (
        await client.get(
            "/api/auth/oidc/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
    ).status_code == 303
    async with AsyncSession(app.state.engine) as session:
        identity = await session.scalar(
            select(OidcIdentity).where(OidcIdentity.subject == "provider-user")
        )
    assert identity is not None
    assert identity.user_id != user.id


async def test_oidc_refuses_to_unlink_the_only_login_method(app, client, monkeypatch) -> None:
    await create_user(app)
    provider = await _provider(app)

    async def exchange_code(*_: object) -> str:
        return "id-token"

    async def validate_id_token(*_: object) -> dict[str, str]:
        return {"sub": "provider-user", "preferred_username": "only-oidc"}

    monkeypatch.setattr("pornarr_api.routers.auth_oidc.exchange_code", exchange_code)
    monkeypatch.setattr("pornarr_api.routers.auth_oidc.validate_id_token", validate_id_token)
    started = await client.get(f"/api/auth/oidc/{provider.id}/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    assert (
        await client.get(
            "/api/auth/oidc/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
    ).status_code == 303
    identity_id = (await client.get("/api/account/oidc")).json()[0]["id"]

    response = await client.delete(f"/api/account/oidc/{identity_id}", headers=csrf_headers(client))

    assert response.status_code == 409
    assert response.json()["code"] == "OIDC_UNLINK_WOULD_LOCK_ACCOUNT"


async def test_oidc_token_validation_rejects_expired_and_wrongly_signed_tokens(
    app, monkeypatch
) -> None:
    provider = await _provider(app)
    signing_key = generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key = generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": "signing-key", "alg": "RS256"})

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"keys": [jwk]}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> Response:
            assert url == "https://issuer.example/jwks"
            return Response()

    monkeypatch.setattr("pornarr_api.oidc.httpx.AsyncClient", lambda **_: Client())
    claims = {
        "sub": "provider-user",
        "iss": provider.issuer,
        "aud": provider.client_id,
        "nonce": "nonce",
    }
    expired = jwt.encode(
        {**claims, "exp": datetime.now(UTC) - timedelta(minutes=1)},
        signing_key,
        algorithm="RS256",
        headers={"kid": "signing-key"},
    )
    wrongly_signed = jwt.encode(
        {**claims, "exp": datetime.now(UTC) + timedelta(minutes=1)},
        wrong_key,
        algorithm="RS256",
        headers={"kid": "signing-key"},
    )
    wrong_nonce = jwt.encode(
        {**claims, "exp": datetime.now(UTC) + timedelta(minutes=1), "nonce": "wrong"},
        signing_key,
        algorithm="RS256",
        headers={"kid": "signing-key"},
    )
    document = {"jwks_uri": "https://issuer.example/jwks"}

    with pytest.raises(OidcAuthenticationError):
        await validate_id_token(expired, provider, document, "nonce")
    with pytest.raises(OidcAuthenticationError):
        await validate_id_token(wrongly_signed, provider, document, "nonce")
    with pytest.raises(OidcAuthenticationError):
        await validate_id_token(wrong_nonce, provider, document, "nonce")
