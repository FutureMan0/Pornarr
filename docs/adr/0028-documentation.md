# 0028 — Documentation lives in docs/

Status: accepted (2026-08-07)

## Context
The original plan file would otherwise sit alongside the new documentation and contradict it.

## Decision
All documentation lives in `docs/` and changes through pull requests like code. Architecture decisions are numbered ADRs. The Nexora plan file is deleted, not archived.

## Consequences
One source of truth. The original plan remains recoverable from git history.
