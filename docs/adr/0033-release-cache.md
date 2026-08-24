# 0033 — release_cache replaces indexer_results

Status: accepted (2026-08-07)

## Context
The plan tied indexer results to a request. The RSS sync produces releases nobody searched for.

## Decision
A cache of releases keyed uniquely by indexer and GUID, independent of any request, expiring on a TTL.

## Consequences
Monitors have something to match against, repeated RSS polls do not reprocess the same release, and search results can be served from cache within the TTL.
