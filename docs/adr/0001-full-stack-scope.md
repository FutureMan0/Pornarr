# 0001 — Build a full stack, not an orchestration layer

Status: accepted (2026-08-07)

## Context
Whisparr already covers indexer search, grabbing and import for adult content. Stash covers library management, metadata and playback via StashDB. Jellyfin covers transcoding. An orchestration layer over those three would be a fraction of the work.

## Decision
Pornarr implements all of it itself: indexer protocols, download-client integration, scanner, metadata matcher, transcoder and recommender.

## Consequences
The largest of the three evaluated scopes, roughly four to six times the orchestration option. In exchange there is no hard dependency on another application, one data model throughout, and one interface. The risk is explicitly accepted; milestones exist so the project has a working state at every step.
