# 0029 — Quality profiles with cutoff and upgrades

Status: accepted (2026-08-07)

## Context
The original plan had no concept of quality ranking, so every grab decision would have been arbitrary and the match score had no frame of reference. This is the core mechanism of Sonarr and Radarr.

## Decision
Ranked quality definitions with size sanity bounds, profiles with an allowed set and a cutoff, custom formats as a scoring system over release properties, and an upgrade path that replaces the file while keeping the logical media record.

## Consequences
Requires splitting the physical file out of the media record; see 0032. Upgrade handling is the most intricate part of the import pipeline and deletes the previous file only after the replacement verifies.
