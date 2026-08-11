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
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.oidc import OidcAuthenticationError, validate_id_token
from pornarr_db.models.oidc import OidcIdentity, OidcProvider
from pornarr_db.types import set_cipher
from pornarr_shared.crypto import CredentialCipher
from tests.api.test_app import SECRET
from tests.api.test_auth import create_user

pytest_plugins = ("tests.api.test_auth",)


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


async def _provider(app) -> OidcProvider:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        provider = OidcProvider(
            name="example",
            issuer="https://issuer.example",
            client_id="client-id",
            client_secret="client-secret",
            scopes=["openid", "profile"],
            discovery_document={
                "issuer": "https://issuer.example",
                "authorization_endpoint": "https://issuer.example/authorize",
                "token_endpoint": "https://issuer.example/token",
                "jwks_uri": "https://issuer.example/jwks",
            },
        )
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
