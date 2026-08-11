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

# `--if-present` so this stage does not fail before the SPA exists (#22). Until
# then the runtime serves WEB_ASSETS_MISSING, which says so explicitly rather
# than 404-ing as if the router were broken.
RUN pnpm --filter @pornarr/web run build --if-present \
    && mkdir -p /web \
    && if [ -d apps/web/dist ]; then cp -r apps/web/dist/. /web/; fi

# ---------------------------------------------------------------------------
# Stage 2 — build a current FFmpeg with the required hardware encoders
# ---------------------------------------------------------------------------
# Alpine is the maintained, small runtime base. Its packaged FFmpeg omits
# NVENC, so build a pinned upstream release with both NVENC and VAAPI instead.
# The proprietary NVIDIA libraries themselves are mounted from the host by the
# NVIDIA container toolkit at runtime.
FROM alpine:3.22 AS ffmpeg-build

ARG FFMPEG_REF=38b88335f99e76ed89ff3c93f877fdefce736c13
ARG NV_CODEC_HEADERS_REF=c69278340ab1d5559c7d7bf0edf615dc33ddbba7

RUN apk add --no-cache \
        build-base \
        git \
        libdrm-dev \
        libva-dev \
        nasm \
        pkgconf \
        x264-dev \
        x265-dev \
        yasm

RUN git clone --depth 1 https://github.com/FFmpeg/nv-codec-headers.git /nv-codec-headers \
    && cd /nv-codec-headers \
    && git fetch --depth 1 origin "$NV_CODEC_HEADERS_REF" \
    && git checkout --detach FETCH_HEAD \
    && make PREFIX=/usr install \
    && git clone --depth 1 https://github.com/FFmpeg/FFmpeg.git /src \
    && cd /src \
    && git fetch --depth 1 origin "$FFMPEG_REF" \
    && git checkout --detach FETCH_HEAD \
    && ./configure \
        --prefix=/opt/ffmpeg \
        --disable-debug \
        --disable-doc \
        --disable-static \
        --enable-ffnvcodec \
        --enable-gpl \
        --enable-libdrm \
        --enable-libx264 \
        --enable-libx265 \
        --enable-nvenc \
        --enable-shared \
        --enable-vaapi \
    && make -j"$(nproc)" \
    && make install

# ---------------------------------------------------------------------------
# Stage 3 — resolve Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.13-alpine3.22 AS python-deps

COPY --from=ghcr.io/astral-sh/uv@sha256:e9a8312ed6a98f515208dd792c61178a0b7c8fbfb807af01534f0e6fe10b24f5 \
    /usr/local/bin/uv /usr/local/bin/uv

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
# Stage 4 — runtime base, shared by development and production
# ---------------------------------------------------------------------------
FROM python:3.13-alpine3.22 AS base

# Apply all currently available Alpine fixes. This is deliberately not
# --ignore-unfixed: the release gate rejects any remaining HIGH or CRITICAL CVE.
RUN apk upgrade --no-cache \
    && apk add --no-cache \
        ca-certificates \
        curl \
        libdrm \
        libstdc++ \
        libva \
        x264-libs \
        x265-libs

COPY --from=ffmpeg-build /opt/ffmpeg /usr/local

# Fixed uid/gid so files written to a bind-mounted /data have predictable
# ownership on the host.
RUN addgroup --gid 1000 pornarr \
    && adduser --disabled-password --gecos '' --home /home/pornarr --uid 1000 --ingroup pornarr pornarr

ENV PATH="/app/.venv/bin:$PATH" \
    LD_LIBRARY_PATH="/usr/local/lib" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY infrastructure/docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["api"]

# ---------------------------------------------------------------------------
# Stage 5 — development: source arrives by bind mount, not by COPY
# ---------------------------------------------------------------------------
FROM base AS development

COPY --from=ghcr.io/astral-sh/uv@sha256:e9a8312ed6a98f515208dd792c61178a0b7c8fbfb807af01534f0e6fe10b24f5 \
    /usr/local/bin/uv /usr/local/bin/uv
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
# Stage 6 — runtime
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
