# 0025 — Administrative setup is delegated

Status: accepted (2026-08-07)

## Context
The repository is owned by FutureMan0. Raphael has write access, which is enough for issues, labels, milestones, branches and workflow files, but not for rulesets, secrets or package permissions.

## Decision
No permission change. Everything achievable with write access is applied directly; the administrative steps are delivered as a single assigned issue containing ready-to-run commands.

## Consequences
Branch protection is not enforced until that issue is completed. Until then the `develop`-only rule for `main` is a convention, not a control.
