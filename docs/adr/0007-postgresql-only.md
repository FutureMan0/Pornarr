# 0007 — PostgreSQL only, with Redis

Status: accepted (2026-08-07)

## Context
The *arr applications ship SQLite, which makes them trivial to run. Supporting both databases doubles the migration and test matrix and forces every query down to the common denominator.

## Decision
PostgreSQL is the only supported database. Redis is the broker, the SSE fan-out bus and the result cache.

## Consequences
Users need three containers instead of one. In exchange `pg_trgm` carries local search, fuzzy title matching and duplicate detection, and concurrent writers — scanner, importer, download monitor — need no coordination.
