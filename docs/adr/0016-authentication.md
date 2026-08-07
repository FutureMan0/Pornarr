# 0016 — Local accounts, OIDC and API keys in v1

Status: accepted (2026-08-07)

## Context
Self-hosted deployments commonly sit behind an identity provider, and scripts need machine access.

## Decision
Argon2id local accounts with opaque Redis-backed sessions in an HttpOnly cookie, CSRF double-submit tokens, and login rate limiting. OIDC with discovery, PKCE, claim-to-role mapping, account linking and just-in-time provisioning ships in the same release. A rotatable per-user API key authenticates machine access.

## Consequences
OIDC is a phase of its own rather than an incidental feature. Three authentication paths must each be tested.
