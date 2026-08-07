# 0009 — Ownership boundary is the OpenAPI document

Status: accepted (2026-08-07)

## Context
Two developers, one of whom works exclusively on the frontend. Without a contract they block each other constantly.

## Decision
FutureMan0 owns `apps/web` and `packages/ui`. Raphael owns everything else. `openapi.json` is generated from FastAPI, committed, and requires approval from both through CODEOWNERS.

## Consequences
The frontend builds against generated types and MSW handlers derived from the contract, so screens exist before endpoints do. CI fails when the committed document drifts from the code.
