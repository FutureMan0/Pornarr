# 0005 — StashDB and TPDB with a filename fallback

Status: accepted (2026-08-07)

## Context
The plan referred vaguely to existing metadata providers. Without a concrete source, matching reduces to parsing filenames, and confidence stays structurally low.

## Decision
`MetadataProviderAdapter` with a StashDB GraphQL adapter, a TPDB REST adapter and an always-on filename parser. Provider keys are stored encrypted in the database and entered by the administrator. A match cascade assigns confidence: fingerprint 0.95, site plus date plus title 0.80, fuzzy title plus performer 0.55, filename alone 0.30.

## Consequences
Pornarr runs without any provider key, but everything then falls below the quarantine threshold. Coverage depends on third-party services and may be thin for niche studios.
