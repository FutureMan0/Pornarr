# 0032 — Separate the logical media record from the file

Status: accepted (2026-08-07)

## Context
The plan stored the file path on the media record. Upgrading a file would then either create a second record or destroy the first.

## Decision
`media` is the logical scene; `media_files` holds the physical file with one active row; `media_file_history` records replacements.

## Consequences
An upgrade keeps the media identifier stable, so tags, favourites, user events and recommendation history all survive. Every query that touches a file path must join, and the active-row constraint is enforced in the database.
