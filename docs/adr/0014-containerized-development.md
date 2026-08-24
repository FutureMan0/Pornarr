# 0014 — Develop inside containers

Status: accepted (2026-08-07)

## Context
A hybrid setup — infrastructure in containers, application processes native — gives the fastest feedback loop, but drifts from production.

## Decision
Everything runs in Docker Compose with bind mounts and reload. The worker receives GPU device passthrough.

## Consequences
Docker Engine, the Compose plugin and the NVIDIA Container Toolkit are prerequisites; none were installed on the reference machine, so this is the first task in the backlog. Debugging requires an exposed debugpy port.
