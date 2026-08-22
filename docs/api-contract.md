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
  "code": "TRANSCODE_LIMIT_REACHED",
  "status": 429,
  "context": { "limit": "per_user" }
}
```

The code is stable and machine-readable. The frontend maps codes to translated
messages; it never renders a server-supplied English string. Adding a code is a
contract change and shows up in the OpenAPI diff.

Representative codes: `RELEASE_EXPIRED`, `RELEASE_FILTERED`, `RELEASE_IN_LIBRARY`,
`TRANSCODE_LIMIT_REACHED`, `DOWNLOAD_CLIENT_CONNECTION_FAILED`,
`INDEXER_CONNECTION_FAILED`, `REQUEST_QUOTA_EXCEEDED`, `ROOT_FOLDER_INVALID`,
`SETUP_REQUIRED`. Every one of them is a code some route raises; a code named here
that nothing raises is worse than a shorter list, because a client may be generated
against it. Not every reason the product records is an HTTP error code: an import
trigger's `duplicate` or `quality_not_in_profile`, and a quarantine item's
`low_confidence`, are fields on those records and never appear in a response body.

### One indexer failing is not the request failing

An indexer search fans out across every configured indexer at once, so no single
indexer's failure can be the response's failure: `POST /api/search/indexers`
answers 202 and `GET /api/search/indexers/{id}` answers 200 whatever the
indexers do. Each one's outcome is reported separately, in the `statuses` map on
the search state, and the vocabulary is `pending`, `completed`, `cached`,
`failed`, `timed_out`, `unhealthy`, `cancelled`, `malformed_response`,
`authentication_failed` and `unavailable`. A status is not an error code: the
frontend translates it as a state the reader can act on, not as a failed request.

## Events

One endpoint, `GET /api/events`, `text/event-stream`, authenticated by the same
session cookie as everything else. Fan-out goes through Redis pub/sub so multiple API
instances all deliver. Event identifiers are Redis stream identifiers, which makes
`Last-Event-ID` resume exact.

```
search.started      search.result_added   search.completed
request.created     request.not_found     download.queued
download.started    download.progress     download.status
download.completed  download.failed       import.started
import.completed    media.available       scan.progress
scan.completed      storage.low_space     settings.changed
notification
```

Every frame the server can publish is named above, because a client is generated from
this document and cannot know about one that is not. `download.status` reports each
change of a job's state while it runs, where `download.completed` and
`download.failed` are published once at its terminal outcome. `request.not_found` ends
a request whose search window expired. `scan.progress` and `scan.completed` report a
library scan. `settings.changed` names the keys an administrator changed, and
`storage.low_space` goes to administrators alone. `notification` is the one frame with
no dotted name: it is a delivered user notification rather than a step in the life of
anything, and like `storage.low_space` it is addressed to a single reader.

Reverse proxies must not buffer this endpoint. The API sets `X-Accel-Buffering: no`
and the deployment documentation states the nginx and Caddy equivalents.

## Authentication

Three paths, all resolving to the same user and role:

- Session cookie, HttpOnly and SameSite=Lax, backed by Redis, for browsers.
- OIDC, which establishes a session on completion.
- `X-Api-Key`, for machines, valid on `/api/*` except `/api/auth/*`.

Mutating requests from a browser require a CSRF double-submit token. API-key requests
do not, because they carry no ambient credential.

`openapi.json` declares both credentials — `SessionCookie` and `ApiKey` — and every
operation that resolves a user carries a `security` block naming the ones it accepts, so
a client generated from the document knows what to present. Operations reachable without
an account carry none: login, the two OIDC entry points, health, reading and redeeming an
invitation, and the setup operations. OIDC is not a scheme in the document because it
presents nothing: it establishes the session cookie.

An administrator deactivates an account with `PATCH /api/admin/users/{id}`. The account's
sessions are revoked in the same request rather than at their next expiry, and an
administrator cannot deactivate their own account — `USER_CANNOT_DEACTIVATE_SELF` — which
is also what keeps an instance from losing its last administrator.
