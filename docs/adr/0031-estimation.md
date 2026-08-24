# 0031 — Prefer the client estimate, fall back to our own

Status: accepted (2026-08-07)

## Context
The plan prescribed one weighted formula for both protocols, including a configured maximum speed that means nothing for torrents, where throughput depends on seeders.

## Decision
For running jobs the download client's own estimate wins. For search results the estimate is protocol-aware, with a seeder factor for torrents and no estimate at all when there are no seeders. Queue time sums the remaining time of equal or higher priority jobs and is recomputed when priorities change.

## Consequences
Every estimate is returned as a range with a confidence level. A single exact figure would claim precision the system does not have, and the interface is built to display the range.
