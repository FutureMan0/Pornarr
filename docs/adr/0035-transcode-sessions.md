# 0035 — Session registry with hard limits, direct play first

Status: accepted (2026-08-07)

## Context
Consumer GPUs cap the number of concurrent encode sessions at driver level. Exceeding it produces an opaque FFmpeg failure.

## Decision
Direct play is checked before any transcode. Sessions live in Redis with a heartbeat from the player. Hardware, software and per-user session limits are detected at startup and enforced, with software fallback when the GPU is saturated and a clear error when everything is.

## Consequences
Most playback never touches the GPU. Sixty seconds without a heartbeat kills FFmpeg and expires the segments; a nightly job removes orphaned directories.
