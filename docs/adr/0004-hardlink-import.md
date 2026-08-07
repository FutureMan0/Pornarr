# 0004 — Hardlink import on a single mount

Status: accepted (2026-08-07)

## Context
The plan specified moving completed files into the library. For torrents this ends seeding immediately, which breaks ratio on private trackers.

## Decision
One volume, `/data`, containing the download directories, the library, quarantine and thumbnails. Import creates a hardlink; if that fails because the paths are on different filesystems, it falls back to copying and warns. Usenet may fall back to moving instead, since nothing seeds.

## Consequences
Users must mount one volume rather than several. A startup health check compares `st_dev` across the configured paths and warns loudly when they differ, because this is the single most common self-hosting misconfiguration.
