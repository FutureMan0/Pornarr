# Workflow

## Branches

```
feature/*  fix/*  chore/*   squash-merge into  develop
develop                     merge-commit into  main
```

`main` accepts pull requests from `develop` only. Nothing else merges there, ever.

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
