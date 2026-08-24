# 0008 — Vite React SPA served by FastAPI

Status: accepted (2026-08-07)

## Context
The plan specified Next.js. Every screen is behind authentication, there is no SEO, and the state is client-heavy, so server rendering earns little.

## Decision
Vite with React and TypeScript in strict mode. The build output is served by FastAPI as static assets with an SPA fallback.

## Consequences
No Node process at runtime and one fewer container. Sub-path deployment behind a reverse proxy works through a configured base path. TanStack Query, Zustand, Zod and Tailwind are unaffected.
