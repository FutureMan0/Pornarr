# Testing

## Shape

`packages/core` is held to 95% line coverage. That is achievable precisely because it
performs no I/O: every formula in the system — normalization, match score, quality
verdict, estimation, deduplication, filters, recommendation scoring — is a pure
function with worked examples in its tests.

Adapters are tested against **recorded responses from real services**: Torznab XML
from Prowlarr, qBittorrent and SABnzbd JSON, StashDB GraphQL. Hand-written mocks
encode what we believe the service does; recordings encode what it actually did.

Integration tests run against real PostgreSQL, Redis, qBittorrent and SABnzbd
containers. They are marked and excluded from the fast path.

End-to-end tests use Playwright and cover the nine flows from the original plan.
axe-core runs inside that suite, so accessibility regressions fail the build rather
than being discovered later.

## What gates what

Every pull request, always:

```
ruff check + ruff format --check
ty check + tsc --noEmit + biome check
pytest -m "not integration"
vitest run
alembic upgrade head, then autogenerate must produce nothing
openapi.json regenerated must equal the committed file
commitlint
```

On matching paths, also blocking: integration tests, and the Docker build for amd64
without pushing.

On `develop`, not blocking pull requests: Playwright, the development image, the
prerelease.

At release: multi-architecture build, Trivy, SBOM.

Coverage is enforced on changed lines at 80%, not globally. A global threshold
punishes the wrong changes and gets disabled the first time it is inconvenient.

## The acquisition stack

Searching an indexer, grabbing a release, handing it to a download client and
importing what comes back is the chain the product exists for, and the default
compose stack has neither an indexer nor a client, so every test of it was
skipped.

```
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.testing.yml up -d
```

That adds qBittorrent and a Torznab indexer with one release. The end-to-end
suite configures both through the same routes an operator uses, and skips the
tests that need them when the file is not running.

A torrent needs a peer and there is none on an isolated network, so the indexer
plays the seeder: it watches the client for the magnet Pornarr just handed it
and supplies the metadata and the data a peer would have. The client verifies
them itself and moves the finished file into the download tree, so everything
from the completion onwards is the product doing its own work. The Web UI has
no password and the release is invented - none of it belongs near a real
deployment.

## Writing tests

Tests state behaviour, not implementation. A test that breaks when a function is
renamed but nothing observable changed is a maintenance cost with no benefit.

Bugs get a failing test first. That is what proves the diagnosis was right.
