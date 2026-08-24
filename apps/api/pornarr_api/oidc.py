"""OIDC discovery and authentication helpers."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx
import jwt

from pornarr_db.models.oidc import OidcProvider
from pornarr_shared.errors import PornarrError

_ALLOWED_ID_TOKEN_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


class OidcDiscoveryError(PornarrError):
    code = "OIDC_DISCOVERY_FAILED"
    status = 422


class OidcAuthenticationError(PornarrError):
    code = "OIDC_AUTHENTICATION_FAILED"
    status = 401


def normalise_issuer(issuer: str) -> str:
    """Accept only a credential-free HTTPS issuer URL without a query or fragment."""
    try:
        parsed = urlsplit(issuer)
        port = parsed.port
    except ValueError as exc:
        raise OidcDiscoveryError("The provider issuer URL is invalid.") from exc
    if (
        issuer != issuer.strip()
        or parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or "?" in issuer
        or "#" in issuer
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise OidcDiscoveryError("The provider issuer URL is invalid.")
    return issuer.rstrip("/")


def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve TCP addresses once so the address checked is the one connected to."""
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def _is_allowed_address(address: str, *, allow_private_issuers: bool) -> bool:
    candidate = ipaddress.ip_address(address)
    if (
        candidate.is_unspecified
        or candidate.is_multicast
        or candidate.is_reserved
        or candidate.is_link_local
    ):
        return False
    if candidate.is_global:
        return True
    return allow_private_issuers and (candidate.is_private or candidate.is_loopback)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve and connect only to allowed addresses for OIDC discovery.

    Passing a validated address to the underlying backend avoids a second DNS
    lookup after the policy check. httpcore keeps the original issuer hostname
    for HTTP Host and TLS SNI handling.
    """

    def __init__(
        self,
        *,
        allow_private_issuers: bool,
        resolver: Callable[[str, int], tuple[str, ...]] = _resolve_addresses,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._allow_private_issuers = allow_private_issuers
        self._resolver = resolver
        self._backend: Any = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await asyncio.to_thread(self._resolver, host, port)
        except OSError as exc:
            raise httpcore.ConnectError("The provider hostname could not be resolved.") from exc

        last_error: Exception | None = None
        for address in addresses:
            if not _is_allowed_address(address, allow_private_issuers=self._allow_private_issuers):
                continue
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise httpcore.ConnectError("The provider could not be reached.") from last_error
        raise httpcore.ConnectError("The provider hostname resolves to a blocked address.")

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


_TIMEOUTS = {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}


async def discover(issuer: str, *, allow_private_issuers: bool = False) -> dict[str, Any]:
    normalised = normalise_issuer(issuer)
    try:
        async with httpcore.AsyncConnectionPool(
            network_backend=GuardedNetworkBackend(allow_private_issuers=allow_private_issuers)
        ) as client:
            response = await client.request(
                "GET",
                f"{normalised}/.well-known/openid-configuration",
                extensions={"timeout": _TIMEOUTS},
            )
        if not 200 <= response.status < 300:
            raise OidcDiscoveryError("The provider discovery document could not be fetched.")
        document = json.loads(response.content)
    except (
        httpcore.ConnectError,
        httpcore.ConnectTimeout,
        httpcore.ReadTimeout,
        httpcore.WriteTimeout,
        httpcore.PoolTimeout,
        httpcore.ProtocolError,
        OSError,
        ValueError,
    ) as exc:
        raise OidcDiscoveryError("The provider discovery document could not be fetched.") from exc
    required = {"issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"}
    if (
        not isinstance(document, dict)
        or not required <= document.keys()
        or not all(isinstance(document[key], str) for key in required)
        or document["issuer"].rstrip("/") != normalised
    ):
        raise OidcDiscoveryError("The provider discovery document is invalid.")
    return document


async def exchange_code(
    provider: OidcProvider,
    document: dict[str, Any],
    code: str,
    verifier: str,
    redirect_uri: str,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                document["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                    "code_verifier": verifier,
                },
            )
            response.raise_for_status()
            token = response.json()["id_token"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise OidcAuthenticationError("The provider could not complete authentication.") from exc
    if not isinstance(token, str):
        raise OidcAuthenticationError("The provider returned an invalid identity token.")
    return token


async def _signing_key(document: dict[str, Any], token: str) -> jwt.PyJWK:
    try:
        header = jwt.get_unverified_header(token)
        key_id = header["kid"]
        if not isinstance(key_id, str):
            raise ValueError
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(document["jwks_uri"])
            response.raise_for_status()
            keys = response.json()["keys"]
        key_data = next(key for key in keys if key.get("kid") == key_id)
        key = jwt.PyJWK(key_data)
    except (
        AttributeError,
        httpx.HTTPError,
        jwt.PyJWTError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
    ) as exc:
        raise OidcAuthenticationError("The provider signing key is invalid.") from exc
    if key.algorithm_name not in _ALLOWED_ID_TOKEN_ALGORITHMS:
        raise OidcAuthenticationError("The provider signing key is invalid.")
    return key


async def validate_id_token(
    token: str, provider: OidcProvider, document: dict[str, Any], nonce: str
) -> dict[str, Any]:
    try:
        key = await _signing_key(document, token)
        claims = jwt.decode(
            token,
            key,
            algorithms=tuple(_ALLOWED_ID_TOKEN_ALGORITHMS),
            audience=provider.client_id,
            issuer=provider.issuer,
            options={"require": ["aud", "exp", "iss", "nonce", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcAuthenticationError("The provider identity token is invalid.") from exc
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise OidcAuthenticationError("The provider identity token is invalid.")
    return claims
