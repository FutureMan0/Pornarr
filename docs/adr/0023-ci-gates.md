# 0023 — Tiered CI gates

Status: accepted (2026-08-07)

## Context
Running everything on every pull request would exhaust the Actions allowance for a private repository within weeks. Running too little lets breakage reach the release branch.

## Decision
Fast checks block every pull request. Integration tests and the Docker build block only when the changed paths warrant them. End-to-end tests, the development image and prereleases run on `develop`. Multi-architecture builds, vulnerability scanning and the SBOM run at release. Coverage is enforced on changed lines, not globally.

## Consequences
Typical pull-request feedback stays around three minutes. A path filter that is too narrow can let a break through, so the filters are reviewed when the layout changes.
