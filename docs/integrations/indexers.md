# Indexers

Pornarr speaks Torznab and Newznab directly. Both are XML over HTTP and share most of
their schema; Torznab adds torrent attributes such as seeders and info hash.

Indexers are rows in the `indexers` table, managed in the administration UI. There is
no indexer configuration in the environment.

```
id, name, protocol (usenet|torrent), implementation, base_url,
api_key_encrypted, categories[], priority, enabled, seed_ratio_limit,
last_error, health_status, last_rss_sync_at
```

Because both protocols are implemented natively, Pornarr works against Prowlarr and
Jackett — which expose exactly these endpoints — without requiring either.

## Search

Every enabled and healthy indexer is queried concurrently, with a per-indexer
timeout. Results are normalized into a common release shape, scored, checked against
the library, filtered by quality profile and content rules, and written to
`release_cache`.

A failing indexer is not an error for the search. It is skipped, its failure is
counted, and the user sees which indexers answered.

## Health

Three consecutive failures mark an indexer unhealthy for five minutes. The
administration UI shows status and the last error verbatim. `indexer_stats` tracks
queries, failures, average latency and grabs per indexer, which is what makes the
indexer priority meaningful.

## RSS sync

Every fifteen minutes each enabled indexer is polled without a search term. New GUIDs
enter `release_cache` and are matched against monitors. This costs one request per
indexer per cycle no matter how many monitors exist, which is the reason monitors are
built on RSS rather than on repeated searches.
