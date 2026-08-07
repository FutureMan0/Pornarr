"""OIDC discovery, kept separate from the later login flow."""

from __future__ import annotations

from typing import Any

import httpx

from pornarr_shared.errors import PornarrError


class OidcDiscoveryError(PornarrError):
    code = "OIDC_DISCOVERY_FAILED"
    status = 422


async def discover(issuer: str) -> dict[str, Any]:
    normalised = issuer.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{normalised}/.well-known/openid-configuration")
            response.raise_for_status()
            document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcDiscoveryError("The provider discovery document could not be fetched.") from exc
    required = {"issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"}
    if not required <= document.keys() or document["issuer"].rstrip("/") != normalised:
        raise OidcDiscoveryError("The provider discovery document is invalid.")
    return document
