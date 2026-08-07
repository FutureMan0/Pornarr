# Architecture

## Module boundaries

```
apps/api        FastAPI: HTTP, authentication, SSE, static serving
apps/worker     arq: background jobs and cron
apps/web        Vite React SPA                            [FutureMan0]

packages/core           pure domain logic, no I/O
packages/db             SQLAlchemy models, Alembic, repositories
packages/integrations   indexer, downloader, metadata, notification adapters
packages/media          ffprobe, ffmpeg, oshash, phash, transcode sessions
packages/shared         configuration, logging, errors, encryption, event bus
packages/ui             design system                     [FutureMan0]
packages/api-client     generated from openapi.json       [generated]
```

`packages/core` is the rule of this codebase: it performs no I/O at all. No database
session, no HTTP client, no filesystem access. Title normalization, match scoring,
quality decisions, estimation, deduplication rules, filter evaluation and
recommendation scoring all live there as pure functions. Both the API and the worker
depend on it, so no rule exists in two places, and the parts that would otherwise be
hardest to test are covered by plain unit tests.

Adapters expose narrow protocols. Nothing that consumes `IndexerAdapter` can tell
Torznab from Newznab; nothing that consumes `DownloaderAdapter` can tell qBittorrent
from SABnzbd. Adding a client is one new file and one new fixture.

## Runtime

One image with four roles: `api`, `worker`, `beat`, `migrate`. Alongside them,
PostgreSQL and Redis. The web build is compiled into the image and served by FastAPI
with an SPA fallback, so there is no Node process in production.

## Data flows

### Search

Local results answer immediately from PostgreSQL using trigram similarity. The
indexer search is enqueued and returns a job identifier. The worker queries every
healthy indexer concurrently with a per-indexer timeout, normalizes and scores the
results, checks each against the library, applies the quality profile and content
filter, and writes survivors to `release_cache`. Each accepted result is published to
the user's event stream as it arrives, so the list fills progressively.

A failing indexer never blocks the others and never blocks the local result.

### Grab

Permission check, release re-validation, duplicate check, then a quality verdict:
grab, upgrade, or reject with a reason. A download client is selected by protocol,
then priority, then health. The job is created and the queue event is emitted.
Polling every five seconds drives progress events. Completion enqueues the import.

### Import

oshash first, because it is cheap and doubles as the metadata lookup key. Then
ffprobe, then the metadata cascade, then the content filter, then a hardlink into the
library. Thumbnails and preview sprites follow. The perceptual hash runs afterwards,
out of the critical path.

### Playback

Direct play is checked first and skips the GPU entirely for most files. Otherwise a
transcode session is created within the configured limits and HLS segments are
served. The player heartbeats; silence kills the process and expires the segments.

## Failure handling

Every indexer and download client has an independent circuit breaker: three
consecutive failures mark it unhealthy for five minutes and it is skipped rather than
allowed to block anything. Health is visible in the administration area with the last
error attached.

Jobs are idempotent through a deterministic job key, so a retry after a restart
cannot import the same file twice. Every job declares a timeout, a maximum attempt
count and exponential backoff.

Errors are structured objects with stable machine-readable codes. The frontend
translates codes; it never displays a server-supplied English string.
