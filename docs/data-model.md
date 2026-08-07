# Data model

Forty tables in PostgreSQL. Sessions and transcode session state live in Redis.

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

## Two departures from the original plan

**`media` is logical, `media_files` is physical.** See [ADR-0032](adr/0032-media-file-split.md).
Replacing a 1080p file with a 2160p one keeps `media.id` stable, so tags, favourites,
user events and recommendation history survive the upgrade. A partial unique index
enforces one active file per media record.

**`release_cache` replaces `indexer_results`.** See [ADR-0033](adr/0033-release-cache.md).
The RSS sync writes releases nobody searched for, which monitors need. Rows expire on
a TTL and `(indexer_id, guid)` is unique.

## Indexing

Trigram GIN indexes on `media.normalized_title` and `release_cache.normalized_title`
carry local search, fuzzy matching and duplicate detection. `pg_trgm` must be enabled
by the first migration.

Hot paths that need covering indexes: `download_jobs` by status and priority,
`user_events` by user and creation time, `release_cache` by indexer and expiry,
`recommendation_candidates` by user and score.

## Secrets

Indexer API keys, download-client passwords, metadata provider keys and OIDC client
secrets are encrypted at rest with a key derived from `APP_SECRET`. No read endpoint
ever returns them, not even to an administrator; the UI shows whether a secret is
set, not what it is.

## Migrations

Alembic only. No schema change reaches a database except through a migration, and CI
fails when the models and the migration history disagree.
