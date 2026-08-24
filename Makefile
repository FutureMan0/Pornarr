.DEFAULT_GOAL := help
SHELL := /bin/bash

## help: list targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'

## setup: create .env from the example and generate a secret
setup:
	@test -f .env || (cp .env.example .env && \
		sed -i "s|^APP_SECRET=.*|APP_SECRET=$$(openssl rand -hex 32)|" .env && \
		echo "wrote .env with a generated APP_SECRET")
	@mkdir -p data backups
	@chmod 0777 data backups 2>/dev/null || true

## up: start the full stack with development overrides
up: setup
	docker compose up -d --wait

## down: stop the stack
down:
	docker compose down

## logs: follow logs for all services
logs:
	docker compose logs -f

## rebuild: rebuild images and restart
rebuild:
	docker compose up -d --build --wait

## scan-image: build and fail on any runtime HIGH or CRITICAL CVE
scan-image:
	docker build --target runtime -t pornarr:local .
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.58.1 image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 pornarr:local

## migrate: apply database migrations
migrate:
	docker compose run --rm migrate

## backup: create a database dump and secret-free bootstrap configuration
backup:
	bash infrastructure/scripts/backup.sh

# `--no-sync` on every `uv run` below, and it is not a speed tweak.
#
# `uv run` re-syncs the project's virtualenv before it runs anything, which
# rewrites files under `/app/.venv`. The development API is started with
# `--reload` and the workers with `--watch /app`, and both watch the whole tree
# — so a `make test` or a `make typecheck` restarted the running API and every
# worker underneath whatever was in flight. Requests answered 2xx and their
# transactions were rolled back with the process. The image already carries a
# synced environment, so there is nothing for these to sync; a dependency change
# is picked up by `make rebuild`.
#
## revision: create a migration from model changes (m="message")
revision:
	docker compose exec api uv run --no-sync alembic revision --autogenerate -m "$(m)"

## shell: open a shell in the api container
shell:
	docker compose exec api sh

## psql: open a database shell
psql:
	docker compose exec postgres psql -U pornarr pornarr

## lint: run all linters and formatters in check mode
lint:
	docker compose exec api uv run --no-sync ruff check .
	docker compose exec api uv run --no-sync ruff format --check .
	docker compose exec web pnpm run lint

## format: apply formatting
format:
	docker compose exec api uv run --no-sync ruff check --fix .
	docker compose exec api uv run --no-sync ruff format .
	docker compose exec web pnpm run format

## typecheck: run type checkers
typecheck:
	docker compose exec api uv run --no-sync ty check
	docker compose exec web pnpm run typecheck

## test: unit tests only
test:
	docker compose exec api uv run --no-sync pytest -m "not integration"
	docker compose exec web pnpm run test

## test-integration: integration tests against real clients
test-integration:
	docker compose -f infrastructure/docker/compose.test.yml up -d --wait
	docker compose exec api uv run --no-sync pytest -m integration
	docker compose -f infrastructure/docker/compose.test.yml down

## test-e2e: Playwright suite against the running stack
test-e2e:
	pnpm exec playwright test

## openapi: regenerate the committed API contract
openapi:
	docker compose exec api uv run --no-sync python -m pornarr_api.scripts.export_openapi > openapi.json
	pnpm --filter @pornarr/api-client run generate

## check: everything CI blocks on, locally
check: lint typecheck test
	docker compose exec api uv run --no-sync alembic check

.PHONY: help setup up down logs rebuild scan-image migrate backup revision shell psql \
        lint format typecheck test test-integration test-e2e openapi check
