# Pornarr — System Design

Status: approved 2026-08-07
Authors: Raphael Bleier (backend), FutureMan0 (frontend/design)
Supersedes: `Nexora – Build-Plan für den Coding Agent.md`

---

## 1. Summary

Pornarr is a self-hosted, multi-user adult media platform. It searches a local
library and external indexers in one query, hands selected releases to a download
client, imports and categorizes the result, plays it back, and learns from user
interaction to recommend and — under explicit limits — automatically acquire more.

It is built as a **full stack**, not as an orchestration layer over existing tools.
Indexer protocols, the download-client integration, the media scanner, the metadata
matcher, the transcoder and the recommender are all implemented in this project. The
alternative — orchestrating Prowlarr, Whisparr, Stash and Jellyfin — was evaluated
and rejected; see ADR-0001.

## 2. Scope

**In scope for v1.0:** local library management, Torznab and Newznab indexer
support, qBittorrent and SABnzbd download clients, an import pipeline with metadata
resolution and quarantine, quality profiles with upgrades, monitored subscriptions
with RSS sync, HLS playback with hardware-accelerated transcoding, local accounts
plus OIDC, per-user content filters, rule-based recommendations, and bounded
automatic downloads.

**Explicitly out of scope for v1.0:** additional download clients (NZBGet,
Transmission, Deluge), collaborative-filtering recommendations, a light theme,
mobile apps, and any form of federation or remote access beyond a reverse proxy.

**Non-goals, permanently:** Pornarr does not host, distribute or index content. It
is a client for infrastructure the operator already runs.

**How this document is used.** This is a system specification, not a single
implementation plan. It is too large to execute in one pass, which is why section 10
decomposes it into ten milestones. Each milestone gets its own implementation plan
written against this document when that milestone starts; nothing here is intended
to be built in one sitting.

## 3. Decision record

Each of these is written up as a numbered ADR under `docs/adr/`. Summarized:

| # | Decision | Consequence |
|---|---|---|
| 1 | Full stack, not an orchestrator | Largest scope of the three options; competes directly with Whisparr, Stash and Jellyfin |
| 2 | Torznab + Newznab implemented directly; multi-indexer in DB | Works against Prowlarr/Jackett without requiring them |
| 3 | qBittorrent + SABnzbd, multiple instances, DB-configured | Protocol-based routing; further clients are new adapters only |
| 4 | Hardlink import on one `/data` mount, copy fallback | Torrents keep seeding; startup health check compares `st_dev` |
| 5 | StashDB + TPDB + filename parser, confidence cascade | Runs without API keys, but everything then lands in quarantine |
| 6 | HLS with VAAPI/QSV/NVENC from v1 | Largest single cost item; hardware matrix accepted as a risk |
| 7 | PostgreSQL only, plus Redis | `pg_trgm` carries search, fuzzy matching and deduplication |
| 8 | Vite + React SPA served by FastAPI | No Node at runtime; one application container |
| 9 | FutureMan0 owns `apps/web` and `packages/ui`; boundary is `openapi.json` | Frontend develops against generated types and MSW mocks |
| 10 | `feature/* → develop` (squash) `→ main` (merge commit) | `main` accepts pull requests only from `develop` |
| 11 | Private now, public at v1.0 | CI minutes are a real constraint until then |
| 12 | MIT, `Copyright (c) 2026 Pornarr Contributors` | Fixed from the first commit |
| 13 | release-please runs on `develop`; `main` tags and publishes | No ruleset bypass; Conventional Commits are mandatory |
| 14 | Everything in containers with bind mounts | Requires Docker Engine and the NVIDIA Container Toolkit locally |
| 15 | One image, role selected by command | `api` / `worker` / `beat` / `migrate`; no version drift |
| 16 | Local accounts and OIDC from v1, plus per-user API keys | OIDC is its own phase, not a side effect |
| 17 | No built-in content blocklist | Operator configures all filtering in the setup wizard |
| 18 | Filter profiles, global and per user; defaults off | A user profile may only tighten the global profile |
| 19 | Nine milestones matching nine phases | One minor release per phase; v1.0 is also the public release |
| 20 | SSE, one multiplexed stream | Fan-out via Redis pub/sub; resume by `Last-Event-ID` |
| 21 | arq, not Celery or Dramatiq | Async-native; matches the rest of the stack |
| 22 | uv workspace (Python 3.13) + pnpm workspace | ruff, ty, biome, tsc |
| 23 | Tiered CI gates | Fast checks always; expensive checks on path match |
| 24 | All issues planned up front | ~143 issues; phases 7–9 will need revision later |
| 25 | Admin steps delegated to FutureMan0 via issue | Branch protection is not active until he applies it |
| 26 | Everything in English; i18n from v0.1 | German only in team conversation |
| 27 | Full label/template/board set with automation | Merge queue deferred until public; stale bot needs exemptions |
| 28 | Docs in `docs/`, ADR-numbered | The Nexora file is deleted, not archived |
| 29 | Quality profiles with cutoff and upgrades | Absent from the original plan; core to every grab decision |
| 30 | Monitors on performer, studio and query, driven by RSS sync | Absent from the original plan; gives priority 60 a meaning |
| 31 | Client ETA preferred; own estimate only as fallback | Estimates are ranges with confidence, never exact seconds |
| 32 | Tiered deduplication: oshash inline, phash in background | Perceptual hashing never deletes automatically |
| 33 | Transcode session registry in Redis; direct play first | Hard session limits with software fallback |
| 34 | Plan's recommendation weights, plus time decay and diversity | Weights live in configuration, not in code |
| 35 | Dark-only restrained design system, rose accent on true black | `PRODUCT.md` and `DESIGN.md` at the repository root |

## 4. Architecture

### 4.1 Module boundaries

```
pornarr/
├── apps/
│   ├── api/            FastAPI: HTTP, auth, SSE, static serving
│   ├── worker/         arq: jobs and cron
│   └── web/            Vite React SPA                    [FutureMan0]
├── packages/
│   ├── core/           pure domain, NO I/O
│   │     naming.py     title normalization, path templating
│   │     matching.py   match score, fuzzy comparison
│   │     quality.py    profiles, cutoff, upgrade decision
│   │     eta.py        estimation and queue time
│   │     dedup.py      duplicate rules
│   │     filters.py    content filter evaluation
│   │     scoring.py    recommendations, auto-download score
│   ├── db/             SQLAlchemy models, Alembic, repositories
│   ├── integrations/   indexer, downloader, metadata, notification adapters
│   ├── media/          ffprobe, ffmpeg, oshash, phash, transcode sessions
│   ├── shared/         config, logging, errors, encryption, event bus
│   ├── ui/             design system                     [FutureMan0]
│   └── api-client/     generated from openapi.json       [generated]
├── docs/
├── infrastructure/
└── docker-compose.yml
```

`packages/core` performs no I/O — no database, no HTTP, no filesystem. Every formula
in this document lives there and is covered by pure unit tests. `apps/api` and
`apps/worker` both depend on it, so no rule is implemented twice.

The adapter packages expose narrow protocols. A consumer of `IndexerAdapter` cannot
tell Torznab from Newznab, and a consumer of `DownloaderAdapter` cannot tell
qBittorrent from SABnzbd. Adding a client means adding one file and one test fixture.

### 4.2 Runtime topology

One image, `ghcr.io/futureman0/pornarr`, whose entrypoint dispatches on a role
argument: `api`, `worker`, `beat`, `migrate`. Production runs those four alongside
PostgreSQL and Redis. The web build is compiled into the image and served by
FastAPI as static assets with an SPA fallback.

FFmpeg is built into the image with NVENC and VAAPI support. NVIDIA driver
libraries are provided at runtime by the container toolkit, so no CUDA SDK ships in
the image.

### 4.3 Data flows

**Search.** The client calls `GET /api/search/local`, which answers immediately from
PostgreSQL using `pg_trgm` similarity. It then calls `POST /api/search/indexers`,
which returns a job identifier with 202 and enqueues the fan-out. The worker queries
every healthy indexer concurrently with a per-indexer timeout, normalizes results,
scores them, checks each against the library for duplicates and upgrade candidates,
applies the quality profile and the content filter, and writes survivors into
`release_cache`. Each accepted result is published to the user's SSE stream as it
lands, so the list fills progressively. A failing indexer never blocks the others
and never blocks the local result.

**Grab.** `POST /api/requests` validates permission, re-validates that the release
still exists, runs the duplicate check, and asks `packages/core.quality` for a
verdict: grab, upgrade, or reject with a reason. On grab it selects a download
client by protocol and priority, creates the `download_jobs` row, and emits
`download.queued`. The `download_poll` cron reads client status every five seconds
and emits `download.progress`. Completion enqueues the import job.

**Import.** oshash first — cheap, and it doubles as the StashDB lookup key. On an
exact match the file is a duplicate and the quality comparison decides between
upgrade and rejection. Otherwise ffprobe extracts technical metadata, the metadata
cascade resolves the scene, the content filter is applied, and the file is
hardlinked into `/data/library/{studio}/{year}/{normalized_title}/{quality}/`.
Thumbnails and preview sprites are generated, `media.available` is emitted, and a
background job computes the perceptual hash.

**Playback.** `GET /api/media/{id}/playback-info` reports whether the client can
play the file directly, given container, codecs, profile and level. Direct play
streams the original file over HTTP range requests. Otherwise a transcode session is
created, subject to hardware, software and per-user session limits, and HLS segments
are served from `/data/transcodes/{session_id}/`. The player heartbeats every
fifteen seconds; sixty seconds of silence kills FFmpeg and expires the segments.

## 5. Data model

Forty tables. Sessions and transcode session state live in Redis, not PostgreSQL.

```
AUTH          users, user_api_keys, oidc_providers, oidc_identities, audit_log
MEDIA         media, media_files, media_file_history, performers, studios, tags,
              media_performers, media_tags
QUALITY       quality_definitions, quality_profiles, quality_profile_items,
              custom_formats, custom_format_conditions
INDEXERS      indexers, indexer_stats, release_cache
DOWNLOADS     download_clients, download_jobs, download_history
REQUESTS      requests, monitors
IMPORT        import_jobs, quarantine_items
FILTERS       content_filter_profiles, content_filter_rules
METADATA      metadata_providers, metadata_match_log
RECOMMEND     user_events, user_preferences, recommendation_candidates,
              automation_rules
SYSTEM        settings, naming_config, notifications, health_checks
```

Two departures from the original plan carry consequences.

**`media` is logical, `media_files` is physical.** The original plan put the file
path on `media`. Upgrades require the split: replacing a 1080p file with a 2160p one
must keep `media.id` stable so tags, favourites, user events and recommendation
history survive. `media_files` holds path, size, codecs, resolution, quality
identifier, custom-format score and `is_active`. `media_file_history` records what
replaced what, and when.

**`indexer_results` becomes `release_cache` and is no longer tied to a request.** The
RSS sync writes releases nobody searched for; without that, monitors have no data to
match against. Rows expire on a TTL, and `(indexer_id, guid)` is unique so repeated
RSS polls do not reprocess the same release.

Secrets — indexer API keys, download-client passwords, metadata provider keys, OIDC
client secrets — are encrypted at rest with a key derived from `APP_SECRET` and are
never returned by any read endpoint.

## 6. Subsystem designs

### 6.1 Quality and upgrades

`quality_definitions` rank resolutions and sources and carry size sanity bounds in
megabytes per minute, which reject mislabelled releases. `quality_profiles` name an
ordered set of allowed qualities, a cutoff above which upgrading stops, and a
minimum custom-format score. `custom_formats` are named scoring rules built from
conditions on title, codec, source, size, indexer or flags.

The grab decision is a pure function:

```
allowed?           -> no  : reject ("not in profile")
score = quality_rank + Σ custom_format_scores
score < profile.min_custom_format_score -> reject
no existing file   -> GRAB
score <= current   -> reject ("not an upgrade")
current >= cutoff  -> reject ("cutoff met")
upgrade_allowed    -> UPGRADE
```

An upgrade imports the new file, activates it, and deletes the old one only after
the new one has passed verification.

### 6.2 Monitors and RSS sync

`monitors` binds a user to a performer, a studio or a saved query, with a quality
profile and a minimum score. The `rss_sync` cron runs every fifteen minutes, pulls
the RSS feed of every enabled indexer without a search term, writes new GUIDs into
`release_cache`, and matches each new release against all enabled monitors. A match
creates a request at priority 60 flagged as automatic, which then flows through the
same quality and filter checks as a manual request.

A daily backlog search runs a real query per monitor to catch releases that appeared
before the monitor existed or were missed while an indexer was unhealthy.

### 6.3 Estimation

For a running job the client's own estimate wins: qBittorrent and SABnzbd know their
seeders, connections and internal queue, and no external model beats that. The
fallback, used only when the client reports nothing, is remaining bytes over a
sixty-second exponential moving average.

For a release with no job yet — the search result list — the estimate is protocol
aware:

```
usenet   : size / ema_line_speed
torrent  : size / (ema_line_speed × seeder_factor)
           seeder_factor = min(1, log10(1 + seeders) / 2)
           seeders == 0 -> no estimate, display "unknown"
```

Queue time is the sum of remaining time across jobs at equal or higher priority, and
is recomputed whenever a priority changes. Total time adds unpacking (usenet only,
from historical seconds per gigabyte) and import (near zero for a hardlink, size over
measured write speed for a copy).

Every estimate is returned as `{min, max, confidence}`. A single exact figure would
claim precision the system does not have.

### 6.4 Deduplication

Blocking, during import: oshash over the first and last 64 KB plus file size gives
an exact match in milliseconds and is also the StashDB lookup key. Then a cheap fuzzy
comparison — title similarity above 0.85, same studio, release dates within two days,
durations within five percent — produces upgrade candidates.

Non-blocking, after import: a perceptual hash over sixteen evenly spaced frames.
A Hamming distance of eight or less records `media.duplicate_of` and raises an
administrator notice. It never deletes anything automatically.

The same fuzzy comparison runs against search results before a grab, so rows already
present are badged rather than hidden.

### 6.5 Transcoding

Direct play is checked first and skips the GPU entirely for the common case.
Sessions live in Redis with a sixty-second TTL refreshed by a fifteen-second
heartbeat from the player. Limits are detected at startup and enforced: hardware
sessions (bounded by the driver — GeForce cards cap concurrent NVENC sessions),
software sessions (cores over two) and per-user sessions (default two). A full GPU
falls back to software; both full returns 429 with an actionable message, never a
raw FFmpeg error.

Orphaned segment directories are removed by the nightly cleanup cron.

### 6.6 Recommendations and automation

The interest profile is rebuilt incrementally by a nightly job. Event weights come
from the original plan and decay exponentially with a ninety-day half-life, so a
profile reflects current interest rather than the first weeks of use. Each axis is
normalized to 0–1.

The score uses the plan's weights, read from configuration rather than compiled in:
0.30 tags, 0.25 performers, 0.15 studios, 0.10 quality preference, 0.10 recency,
0.10 general popularity, minus hard blocks. Ranking then enforces diversity — at most
two entries per performer and three per studio in the top twenty — and reserves
twenty percent of slots for exploration.

Every candidate stores a structured `reason_json` (matched tags, matched performers,
dominant factor, score breakdown). The sentence is composed in the frontend so it can
be translated.

Automatic downloads are off by default and require all of: automation enabled for
that user, score above the threshold, no blocked tag, no duplicate, daily size budget
not exceeded, at least fifteen percent free disk, quality within the profile, and
sufficient metadata confidence. Defaults: 10 GB per user per day, two concurrent
automatic jobs, three automatic downloads per day. Manual requests always preempt.

### 6.7 Content filters

There is no built-in blocklist. `content_filter_profiles` exist at global and user
scope; `content_filter_rules` carry a kind (term, tag, performer, minimum metadata
confidence, unknown performer age, unknown file type), a pattern, an action
(`allow`, `quarantine`, `reject`) and an enabled flag. Every rule ships disabled and
empty; the setup wizard is where an operator turns them on.

A user profile may only tighten the global profile, never loosen it. Every filter
action writes to `audit_log` with the rule that fired.

### 6.8 Authentication

Local accounts use Argon2id. Sessions are opaque identifiers in Redis behind an
HttpOnly, SameSite=Lax cookie, so revocation is immediate. CSRF uses a double-submit
token on every mutating request. Login is rate limited per IP and account.

OIDC ships in the same release: discovery, PKCE, claim-to-role mapping, account
linking and just-in-time provisioning.

Machine access uses a per-user `X-Api-Key`, stored hashed and rotatable, valid on
`/api/*` except `/api/auth/*`, carrying the owning user's role and tagged as
`source=apikey` in the audit log.

### 6.9 Events

One SSE endpoint, `GET /api/events`, authenticated by the same session cookie as
every other route. The server subscribes to `pornarr:user:{id}:*` and
`pornarr:global:*` in Redis, so multiple API instances all deliver. Event
identifiers are Redis stream identifiers, which makes `Last-Event-ID` resume exact
rather than approximate.

Reverse proxies must disable buffering; the deployment documentation states this and
the API sets `X-Accel-Buffering: no`.

## 7. API contract

`openapi.json` is generated from FastAPI, committed, and checked for drift in CI. It
is the contract between the two developers, and `CODEOWNERS` requires both to
approve changes to it. `packages/api-client` is generated from it, and MSW handlers
are derived from it so the frontend can build screens before an endpoint exists.

Errors are structured objects with a stable machine-readable code
(`INDEXER_TIMEOUT`, `HARDLINK_CROSS_DEVICE`, `TRANSCODE_LIMIT_REACHED`,
`QUALITY_CUTOFF_MET`, …), an HTTP status, and optional context. The frontend
translates codes; it never displays a server-supplied English string.

## 8. Error handling

Every indexer and download client has an independent circuit breaker: three
consecutive failures mark it unhealthy for five minutes and it is skipped rather
than allowed to block a search. Health status is visible in the administration area
with the last error attached.

Jobs are idempotent through a deterministic job key, so a retry after a worker
restart cannot import the same file twice. Every job declares a timeout and a
maximum attempt count with exponential backoff. Failed downloads follow the plan:
record the error, temporarily block that release, search for an alternative, retry
at most twice, then notify the user and the administrator.

Structured logging throughout, with user interests never written in clear text.

## 9. Testing

`packages/core` is held to 95% line coverage, which is achievable precisely because
it is pure. Every formula in this document has a test with worked examples.

Adapters are tested against recorded responses from real services — Torznab XML from
Prowlarr, qBittorrent and SABnzbd JSON, StashDB GraphQL — rather than hand-written
mocks, so a fixture drifting from reality is visible.

Integration tests run against real PostgreSQL, Redis, qBittorrent and SABnzbd
containers. Playwright covers the nine flows from the original plan and runs on
`develop`, not on every pull request. axe-core runs inside the Playwright suite.

Diff coverage of 80% on changed lines gates every pull request; global coverage does
not, because a global threshold punishes the wrong changes.

## 10. Delivery

| Milestone | Content | Issues |
|---|---|---|
| v0.1 | Monorepo, compose, local auth, health checks, CI | 16 |
| v0.2 | OIDC, setup wizard, filter profiles, API keys | 12 |
| v0.3 | Scanner, ffprobe, oshash, thumbnails, local search | 14 |
| v0.4 | Direct play, HLS, NVENC/VAAPI/QSV, sessions | 12 |
| v0.5 | Torznab, Newznab, multi-indexer, match score | 13 |
| v0.6 | qBittorrent, SABnzbd, queue, ETA, SSE | 12 |
| v0.7 | Import pipeline, metadata, quarantine, quality profiles | 18 |
| v0.8 | Requests, monitors, RSS sync, priorities | 13 |
| v0.9 | Events, interest profile, ranking, explanations | 9 |
| v1.0 | Automation, backups, monitoring, public release | 14 |
| | Epics and the admin setup issue | 10 |
| | **Total** | **143** |

Work splits by ownership: `apps/web` and `packages/ui` to FutureMan0, everything
else to Raphael. Cross-boundary work is coordinated through `openapi.json`.

## 11. Delivery pipeline

`feature/*` branches squash-merge into `develop`. `develop` merges into `main` as a
merge commit, and `main` accepts pull requests from no other source.

release-please runs on `develop` and opens its release pull request there, so no bot
ever writes to `main` and the branch rule needs no exception. When that release pull
request merges, `develop` carries the new version and changelog; merging `develop`
into `main` triggers tagging, the multi-architecture build, the GHCR push and the
GitHub release.

Pull requests always run linting, formatting, type checking, unit tests, migration
consistency, OpenAPI drift and commit-message linting. Integration tests and the
Docker build run when the changed paths warrant them. End-to-end tests, the
`develop` image and prereleases run on `develop`. Multi-architecture builds, Trivy
and the SBOM run at release.

## 12. Risks

**Hardware transcoding is the largest single risk.** Driver versions, `/dev/dri`
passthrough, container permissions and per-GPU codec support form a matrix that
cannot be fully tested in CI. Mitigation: startup capability detection, software
fallback on every path, and an explicit support statement in the documentation.

**Branch protection is not enforced until FutureMan0 applies it.** Until then, the
`develop`-only rule for `main` is a convention. Issue #1 carries the exact commands.

**Metadata coverage depends on third-party services.** StashDB and TPDB require
operator-supplied keys and may not cover niche studios. The confidence cascade
degrades to quarantine rather than to wrong data.

**The issue backlog will age.** Phases 7 to 9 are planned before phase 1 exists and
will need revision. This was chosen deliberately over just-in-time planning.

**Scope is large for two developers, one of whom is frontend-only.** The milestone
structure exists so the project has a working, tagged state at every step and can
stop at any milestone without leaving something half-built.

**The design system is derived from a pattern family, not from a live audit of a
reference site.** If pixel-exact reference is wanted, FutureMan0 supplies screenshots
and `DESIGN.md` is revised.
