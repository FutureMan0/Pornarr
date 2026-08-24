# 0018 — Filter profiles, global and per user

Status: accepted (2026-08-07)

## Context
The plan foresaw per-user hiding of tags and performers, which a single instance-wide list cannot express.

## Decision
`content_filter_profiles` exist at global and user scope. `content_filter_rules` carry a kind, a pattern, an action (allow, quarantine, reject) and an enabled flag. Every rule ships disabled and empty. A user profile may only tighten the global profile.

## Consequences
Two scopes mean a resolution step on every evaluation. Every filter action writes an audit entry naming the rule that fired.
