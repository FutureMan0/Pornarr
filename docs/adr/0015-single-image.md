# 0015 — One image, role selected by command

Status: accepted (2026-08-07)

## Context
The worker needs FFmpeg and codec support; the API does not. Separate images keep the API small but must be kept in lockstep.

## Decision
One image whose entrypoint dispatches on `api`, `worker`, `beat` or `migrate`.

## Consequences
One build, one cache, one tag, one pull for the user, and no possibility of version drift between API and worker. The image is larger than a split API image would be. NVIDIA driver libraries come from the host at runtime, so no CUDA SDK is bundled.
