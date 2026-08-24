# 0017 — No built-in content blocklist

Status: accepted (2026-08-07)

## Context
A hardcoded, non-disableable blocklist was proposed as a floor that survives misconfiguration. The alternative is leaving all filtering to the operator.

## Decision
Pornarr ships no built-in blocklist. All filtering is configured by the operator in the setup wizard.

## Consequences
An instance with no configured rules performs no content filtering. The concern was raised once during planning and the decision was taken deliberately by the project owner; it is recorded here rather than revisited.
