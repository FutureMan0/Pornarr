# API contract

`openapi.json` is generated from FastAPI, committed to the repository, and checked
for drift in CI. It is the boundary between the two developers and requires approval
from both under CODEOWNERS.

`packages/api-client` is generated from it. MSW handlers are derived from it, so the
frontend can build screens before the endpoints exist.

## Conventions

All application endpoints live under `/api`. Request and response bodies are
validated with Pydantic on the server and Zod on the client. Every list endpoint
paginates with a cursor, never an offset.

## Errors

Errors are objects, not strings:

```json
{
  "code": "HARDLINK_CROSS_DEVICE",
  "status": 422,
  "context": { "source": "/data/torrents/x", "target": "/data/library/y" }
}
```

The code is stable and machine-readable. The frontend maps codes to translated
messages; it never renders a server-supplied English string. Adding a code is a
contract change and shows up in the OpenAPI diff.

Representative codes: `INDEXER_TIMEOUT`, `INDEXER_UNHEALTHY`, `RELEASE_EXPIRED`,
`DUPLICATE_IN_LIBRARY`, `QUALITY_NOT_IN_PROFILE`, `QUALITY_CUTOFF_MET`,
`HARDLINK_CROSS_DEVICE`, `METADATA_CONFIDENCE_LOW`, `FILTER_REJECTED`,
`TRANSCODE_LIMIT_REACHED`, `DOWNLOAD_CLIENT_UNREACHABLE`.

## Events

One endpoint, `GET /api/events`, `text/event-stream`, authenticated by the same
session cookie as everything else. Fan-out goes through Redis pub/sub so multiple API
instances all deliver. Event identifiers are Redis stream identifiers, which makes
`Last-Event-ID` resume exact.

```
search.started      search.result_added   search.completed
request.created     download.queued       download.started
download.progress   download.completed    download.failed
import.started      import.completed      media.available
```

Reverse proxies must not buffer this endpoint. The API sets `X-Accel-Buffering: no`
and the deployment documentation states the nginx and Caddy equivalents.

## Authentication

Three paths, all resolving to the same user and role:

- Session cookie, HttpOnly and SameSite=Lax, backed by Redis, for browsers.
- OIDC, which establishes a session on completion.
- `X-Api-Key`, for machines, valid on `/api/*` except `/api/auth/*`.

Mutating requests from a browser require a CSRF double-submit token. API-key requests
do not, because they carry no ambient credential.
