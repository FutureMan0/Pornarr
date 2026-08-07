# 0003 — qBittorrent and SABnzbd, multiple instances

Status: accepted (2026-08-07)

## Context
Supporting both protocols requires a client for each. The original plan again assumed a single client configured by environment variable.

## Decision
qBittorrent and SABnzbd adapters ship in v1, configured as rows in `download_clients`. Multiple instances per protocol are supported, and a grab is routed by protocol, then priority, then health.

## Consequences
Status-code mapping between each client and Pornarr's own lifecycle is the most error-prone part of the integration and is covered by recorded-response tests. NZBGet, Transmission and Deluge can be added later as adapters without touching the core.
