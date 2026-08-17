# Workflow

## Branches

```
feature/*  fix/*  chore/*   squash-merge into  develop
develop                     merge-commit into  main
```

`main` accepts pull requests from `develop` only. Nothing else merges there, ever.
The **Only develop may merge to main** check enforces this; an administrator must
mark it required in the `main` ruleset (see issue #1).

Branch names are lowercase with hyphens and carry the issue number when one exists:
`feat/142-torznab-adapter`.

## Commits

Conventional Commits, and not as a style preference: the type determines the released
version. `feat` produces a minor release, `fix` a patch, `feat!` or a
`BREAKING CHANGE:` footer a major. `docs`, `chore`, `refactor`, `test`, `style`, `ci`
and `build` release nothing.

A bug fix committed as `chore` never reaches users.

## Pull requests

One approval required. The pull-request title must itself be conventional, because
squash-merging into `develop` makes it the commit message the release tooling reads.

CODEOWNERS routes review automatically: `apps/web` and `packages/ui` to FutureMan0,
everything else to Raphael, `openapi.json` to both.

## Ownership

The API contract is the boundary. When a change needs a new endpoint, the contract
changes first, both owners approve it, and then both sides can proceed in parallel —
the frontend against generated types and MSW mocks, the backend against the real
implementation.

## Looking at the application

```bash
docker compose up -d --wait          # postgres, redis, api, workers, vite
docker compose exec api python /app/scripts/seed_demo.py
```

Then <http://localhost:5173>, signing in as `kai` with
`correct horse battery staple`. The seed also creates four guests — `jonas`,
`mira`, `tobi`, `lea` — with the same password, which is how to see what a
non-administrator's navigation looks like.

`seed_demo.py` refuses a database that already holds titles. It is for looking
at screens, not for fixtures: every screen in this application is a view of
data, and an empty server shows a dozen honest empty states and nothing else.

The compose stack expects the download directories to exist under the data
volume. Nothing creates them:

```bash
mkdir -p data/torrents data/usenet data/quarantine data/thumbnails backups
```
