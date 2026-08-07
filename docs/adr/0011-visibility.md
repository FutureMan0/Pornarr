# 0011 — Private now, public at v1.0

Status: accepted (2026-08-07)

## Context
Actions minutes and package storage are free for public repositories and metered for private ones. Docker builds with FFmpeg consume them quickly.

## Decision
The repository stays private until v1.0, then goes public along with the container images.

## Consequences
Until then CI must be frugal: aggressive build caching, single-architecture builds in pull requests, multi-architecture only at release. Community health files are added immediately so the transition is a switch rather than a project.
