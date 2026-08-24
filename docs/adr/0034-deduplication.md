# 0034 — Tiered deduplication

Status: accepted (2026-08-07)

## Context
The plan listed eight comparison signals, two of which — a full-file checksum and a video fingerprint — are far too expensive to run inside an import.

## Decision
oshash and a cheap fuzzy comparison run inline and block the import. Perceptual hashing runs afterwards as a background job and only records a candidate relationship.

## Consequences
Import stays fast enough for bulk first-run scans. Perceptual matches never delete anything automatically; they raise an administrator notice. oshash doubles as the StashDB lookup key, so the fingerprint work is not wasted.
