# One image, four roles: api, worker, beat, migrate. See docs/adr/0015-single-image.md
#
# The web build is compiled in and served by FastAPI, so production runs no Node
# process. FFmpeg carries NVENC and VAAPI support; the NVIDIA driver libraries
# come from the host at runtime through the container toolkit, so no CUDA SDK is
# bundled and the image stays a reasonable size.

# ---------------------------------------------------------------------------
# Stage 1 — build the frontend
# ---------------------------------------------------------------------------
FROM node:22-slim AS web-build

WORKDIR /build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/ui/package.json packages/ui/
COPY packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile

COPY tsconfig.base.json tsconfig.json biome.json openapi.json ./
COPY apps/web apps/web
COPY packages/ui packages/ui
COPY packages/api-client packages/api-client

# The guard here was scoped to "before the SPA exists (#22)", and #22 is this
# change. It has to go, not merely because it is spent: pnpm forwards the flag
# to the script now that one exists, and Vite's CLI rejects it as `--ifPresent`.
#
# A missing web build is now a real failure. Letting it through would produce an
# image that starts and answers WEB_ASSETS_MISSING, which is a worse place to
# discover the problem than the build.
RUN pnpm --filter @pornarr/web run build \
    && mkdir -p /web \
    && cp -r apps/web/dist/. /web/

# ---------------------------------------------------------------------------
# Stage 2 — resolve Python dependencies
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-deps

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Manifests first: dependencies only re-resolve when a manifest changes, not on
# every source edit.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY packages/core/pyproject.toml packages/core/
COPY packages/db/pyproject.toml packages/db/
COPY packages/integrations/pyproject.toml packages/integrations/
COPY packages/media/pyproject.toml packages/media/
COPY packages/shared/pyproject.toml packages/shared/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev

COPY apps apps
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 3 — runtime base, shared by development and production
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS base

# ffmpeg: Debian's build carries VAAPI and NVENC. curl: container healthchecks.
# The NVIDIA user-space libraries are mounted from the host by the container
# toolkit, which is why nothing CUDA is installed here.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid so files written to a bind-mounted /data have predictable
# ownership on the host.
RUN groupadd --gid 1000 pornarr \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash pornarr

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY infrastructure/docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["api"]

# ---------------------------------------------------------------------------
# Stage 4 — development: source arrives by bind mount, not by COPY
# ---------------------------------------------------------------------------
FROM base AS development

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    APP_ENV=development

COPY --from=python-deps --chown=pornarr:pornarr /app/.venv /app/.venv
# `uv pip`, not `python -m pip`: a uv-created virtual environment has no pip in
# it, so the usual invocation fails with "No module named pip".
RUN uv pip install --python /app/.venv/bin/python --no-cache debugpy

RUN mkdir -p /data && chown -R pornarr:pornarr /data /app
USER pornarr

# ---------------------------------------------------------------------------
# Stage 5 — runtime
# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=python-deps --chown=pornarr:pornarr /app/.venv /app/.venv
COPY --chown=pornarr:pornarr apps apps
COPY --chown=pornarr:pornarr packages packages
COPY --chown=pornarr:pornarr alembic alembic
COPY --chown=pornarr:pornarr alembic.ini pyproject.toml ./
COPY --from=web-build --chown=pornarr:pornarr /web /app/apps/api/pornarr_api/static

RUN mkdir -p /data && chown -R pornarr:pornarr /data /app
USER pornarr

EXPOSE 8000
