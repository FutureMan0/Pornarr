# Metadata providers

Three sources, tried in order of reliability.

**StashDB** — GraphQL, community-maintained, supports fingerprint lookup by oshash and
perceptual hash. The most precise match available, because a fingerprint hit is not a
guess.

**TPDB** — REST, matched on site, date and title. Broader coverage of commercial
studios, weaker on fingerprints.

**Filename parser** — always active, never disabled. Extracts studio, date, performers
and title from release names, folder names and NFO files using a regex cascade.
Confidence never exceeds 0.4.

Provider keys are stored encrypted in `metadata_providers` and entered by the
administrator. Nothing is bundled.

## Provider precedence

Within a confidence tier, StashDB is authoritative and TPDB is queried only if
StashDB has no usable result. The cascade enforces this ordering even when adapters
are supplied in a different order. It does not merge conflicting fields: the first
provider at the winning tier owns the complete candidate. A higher-confidence TPDB
site/date/title match still correctly beats a lower-confidence StashDB fuzzy match.

## Confidence cascade

```
fingerprint (oshash or phash) exact   0.95
site + date + title                   0.80
fuzzy title + performer               0.55
filename only                         0.30
```

The resulting confidence feeds the content filter, which decides whether a media item
enters the library or quarantine. With no provider key configured, everything scores
at most 0.4 — Pornarr still runs, but nothing is auto-classified.

Every attempt is written to `metadata_match_log` with the provider, the query, the
result and the score, so a wrong match can be traced rather than guessed at.

## Rate limits

Both providers are rate limited. Lookups are queued rather than fired per import, with
per-provider concurrency and a backoff that respects `Retry-After`. Metadata
refreshing is a background job, never part of the import critical path.
