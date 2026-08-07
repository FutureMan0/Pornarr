# 0013 — release-please runs on develop

Status: accepted (2026-08-07)

## Context
release-please and semantic-release both want to write to the release branch, which conflicts with the rule that only `develop` may merge into `main`.

## Decision
release-please runs on `develop` and opens its release pull request there. When it merges, `develop` carries the new version and changelog. Merging `develop` into `main` then triggers tagging, the multi-architecture build and the GitHub release.

## Consequences
No ruleset exception and no bot writing to `main`. Conventional Commits become mandatory, because the commit type determines the version.
