# 0002 — Implement Torznab and Newznab directly

Status: accepted (2026-08-07)

## Context
The original plan configured a single indexer through two environment variables, which implied one Prowlarr instance and contradicted the full-stack decision.

## Decision
Both protocols are implemented in Pornarr. Indexers are rows in the database, managed through the administration UI, not environment variables. Usenet and torrent are both supported.

## Consequences
Pornarr works against Prowlarr and Jackett, since both expose these protocols, without requiring either. Every downstream subsystem must be protocol-aware: estimation, queue semantics, import mode and seeding all differ between the two.
