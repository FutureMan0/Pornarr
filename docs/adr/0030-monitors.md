# 0030 — Monitors on performer, studio and query

Status: accepted (2026-08-07)

## Context
The plan assigned priority 60 to subscribed content but never defined what a subscription is.

## Decision
A monitor binds a user to a performer, a studio or a saved query, with a quality profile and a minimum score. An RSS sync polls every enabled indexer every fifteen minutes and matches new releases against all monitors, creating automatic requests at priority 60. A daily backlog search catches what RSS missed.

## Consequences
RSS costs one request per indexer per cycle regardless of how many monitors exist, which repeated searches would not. `release_cache` must therefore be independent of requests; see 0033.
