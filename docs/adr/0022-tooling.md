# 0022 — uv and pnpm workspaces, Python 3.13

Status: accepted (2026-08-07)

## Context
The reference machine has Python 3.14, but the ecosystem does not yet publish wheels for everything on it, which would force source builds inside the image and in CI.

## Decision
uv manages a Python workspace pinned to 3.13; pnpm manages the JavaScript workspace. Both lockfiles are committed. Linting and formatting use ruff and biome; type checking uses ty and tsc.

## Consequences
uv provides its own interpreter, so the host version is irrelevant. Docker builds stay fast, which matters while Actions minutes are metered.
