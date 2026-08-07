# 0027 — Full template and automation set

Status: accepted (2026-08-07)

## Context
A public repository receives reports without version, logs or environment unless the templates ask for them.

## Decision
Four label axes (area, type, priority, meta), YAML issue forms, a pull-request template, CODEOWNERS, Dependabot, path-based auto-labelling, reviewer assignment, a stale bot and semantic pull-request title checking.

## Consequences
Merge queues are unavailable for a private repository on a personal account and are deferred until publication. The stale bot must exempt epics, anything with a milestone and anything assigned, or it would close the planned backlog.
