# Pornarr

Self-hosted, multi-user adult media platform. Searches your local library and your
indexers in one query, hands releases to your download client, imports and categorizes
the result, plays it back, and learns what to suggest next.

> Status: pre-alpha. Nothing here is usable yet. See the
> [milestones](https://github.com/FutureMan0/Pornarr/milestones) for what exists.

## What it does

- One search across the local library and every configured indexer, with local hits
  answering instantly and external hits filling in as they arrive
- Torznab and Newznab natively, so it works with or without Prowlarr
- qBittorrent and SABnzbd, with several instances per protocol
- Quality profiles with cutoffs, custom formats and automatic upgrades
- Monitors on a performer, a studio or a saved query, driven by RSS
- Import with metadata resolution, deduplication, configurable filtering and
  quarantine
- Playback with direct play where possible and hardware-accelerated HLS where not
- Per-user recommendations, each with a stated reason
- Bounded automatic acquisition, off by default

Every user gets their own profile, filters, requests and recommendations. The
administrator sees and controls the whole instance.

## Requirements

Docker with the Compose plugin, one filesystem for downloads and library, and
optionally a GPU for transcoding. Start with the
[installation guide](docs/operations/installation.md); it covers the one-mount
rule, GPU passthrough, reverse proxies, upgrades, and common failures.

## Documentation

[docs/](docs/) — architecture, data model, API contract, integrations, pipelines,
operations, and every architecture decision as a numbered ADR.

## Legal

Pornarr does not host, index or distribute content. It is a client for infrastructure
you already run, and it is for legally available material only. Content filtering is
configured by you; nothing is filtered by default.

## Licence

MIT. See [LICENSE](LICENSE).
