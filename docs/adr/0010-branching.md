# 0010 — feature to develop to main

Status: accepted (2026-08-07)

## Context
The team wants a stable branch and a development branch, with releases from both.

## Decision
Feature branches squash-merge into `develop`. `develop` merges into `main` as a merge commit, preserving the commit history the release tooling reads. `main` accepts pull requests from no source other than `develop`.

## Consequences
Rulesets enforce this rather than convention; see 0025 for why it is not yet active. Linear history must stay disabled on `main` so the merge commit is possible.
