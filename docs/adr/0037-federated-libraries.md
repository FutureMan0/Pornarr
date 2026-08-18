# 0037 — Federated libraries: browse a friend's shelf, hold none of it

Status: accepted (2026-08-18)

## Context
Several households each run their own Pornarr behind a Cloudflare Tunnel and want one
combined library: browse and search everything, play a title that lives on somebody
else's box. Nobody wants to hand over their database, their accounts or their files,
and nobody wants a central service in the middle. What they are willing to share is
*read access*, revocable, to what their own instance already exposes over HTTP.

## Decision
A **peer** is a row: a name, a base URL and an API key that the *remote* instance
issued from its own account screen. Nothing else about a peer is stored — no copy of
its catalogue, no mirrored artwork, no user accounts. The key is written through
`EncryptedString`, the same column type an indexer key uses, and is never read back
out: not into a response body, not into an error, not into a log line. A failed test
records a short code (`unreachable`, `timed_out`, `unauthorized`, `invalid_response`)
because an exception message carries the URL the key was sent to.

Browsing reads from several sorted lists at once. Each source keeps its own position,
the positions travel together as one opaque cursor, and a page is produced by a k-way
merge (`pornarr_db.peers`). The merge consumes a prefix of each source and advances
that source's offset by exactly what it used, so no item can be shown twice or stepped
over — that property holds even where the sort key is only approximate across
instances, which is the case for `sort=title` (each instance orders by its own
`normalized_title`, and only the visible title crosses the wire).

The browser never gets a key. Anything that lives on a peer is fetched by this
instance through `/api/peers/{id}/proxy/{path}`, where `path` must match a fixed
allowlist of read paths plus the three calls that drive a transcode of the reader's
own playback. Everything else is 404, including a disabled peer and an unknown one.
Range requests and bodies stream through untouched; response headers are an
allowlist, so a peer cannot set a cookie in a browser it has no relationship with.

A peer that does not answer within its timeout is dropped from the page and named in
`unavailable_peers`. Its offset stays in the cursor, so it rejoins the next page
rather than having been paged past while it was down.

Three fields were added to the library contract to make the above honest rather than
approximate: `added_at` on an item (the default sort has no comparable key across
instances without it), `total` on a page (which is where a peer's `media_count`
comes from), and `unavailable_peers`.

## What this deliberately refuses to do
- **No mirroring.** Nothing a peer answers is written to this database. A peer is a
  source of bytes to forward, never a source of rows. That also means a compromised
  peer cannot plant records here.
- **No open relay.** The proxy will not fetch an arbitrary path, an arbitrary host,
  or on behalf of an anonymous caller: it requires a session, and unsafe methods still
  pass CSRF. A household's tunnel does not become an anonymous mirror of a friend's
  server.
- **No transitive federation.** Every fan-out asks a peer for `source=local`. Friends
  of friends are not in the library, and a ring of three instances does not turn one
  page request into an amplification loop.
- **No shared identity.** Ratings, comments, requests, watchlists and playback progress
  stop at the instance boundary. Remote items come back with empty progress on
  purpose: the peer knows the progress of the account whose key we hold, and that
  account is a shared service account, not the person reading the page.
- **No trust in a peer's answers.** A page is parsed into a strict model before it is
  merged, out-of-range numbers and all; a page that does not parse makes the peer
  unavailable for that request rather than half-merged. A peer's own `poster_url` is
  not read at all — artwork URLs are rebuilt from the id, so a peer cannot point a
  reader's browser at a third party.

## Consequences
Browsing everything costs one request per peer per page, in parallel, each bounded by
a timeout; the slowest healthy peer sets the page latency. `media_count` is a snapshot
from the last successful test, not a live number. Revoking access is the remote
instance revoking its own API key — no coordination needed, and the next call simply
degrades to "this peer is unavailable".
