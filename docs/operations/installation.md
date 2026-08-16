# Installation

Pornarr is deployed with Docker Compose. This guide takes a new host from the
repository checkout to a healthy local instance at `http://localhost:8000`.

## Before you begin

- Docker Engine with the Compose plugin
- a directory on one filesystem for downloads, the library, thumbnails, and
  transcodes
- an optional NVIDIA, Intel, or AMD GPU if hardware transcoding is required

The first release supports Docker Compose on a 64-bit Linux host. The included
Compose file starts PostgreSQL 17 and Redis 8; no separate database installation is
needed.

## Start a local instance

Clone the repository and create separate host directories for media and backups:

```sh
git clone https://github.com/FutureMan0/Pornarr.git
cd Pornarr
mkdir -p /srv/pornarr/data /srv/pornarr/backups
make setup
```

`make setup` creates `.env` and generates `APP_SECRET`. Set the two host paths in
that file before starting:

```sh
$EDITOR .env
# DATA_PATH_HOST=/srv/pornarr/data
# BACKUP_PATH_HOST=/srv/pornarr/backups
```

`APP_SECRET` encrypts every saved integration credential. Back it up with the
database: a restored database without the original secret cannot decrypt its
credentials.

Start the stack and wait for the API health check:

```sh
docker compose up -d --wait
docker compose ps
docker compose logs -f api
```

Open `http://localhost:8000`. The first administrator account and runtime
configuration are created in the browser. Do not expose port 8000 directly to
the Internet; use a TLS reverse proxy instead and restrict port 8000 with the host
firewall to the proxy.

## The one-mount rule

Downloads and the library must share **one filesystem**. Compose mounts one host
directory at `/data`; keep every managed path under it:

```text
/srv/pornarr/data
├── torrents/
├── usenet/
├── library/
├── quarantine/
├── thumbnails/
└── transcodes/
```

Point qBittorrent categories under `/data/torrents` and SABnzbd categories under
`/data/usenet`, using paths as seen by both Pornarr and the download client.
Pornarr imports into `/data/library`.

This works because hardlinks stay on the same filesystem: imports are fast, use
no extra disk space, and torrents can keep seeding. Do not mount a downloads
directory and library directory separately:

```text
# Incorrect: these may be different filesystem devices.
/downloads -> /downloads
/library   -> /library
```

That arrangement forces every import to copy data. If startup warns that the
paths have different devices, fix the mounts before importing media.

## Hardware transcoding

GPU access belongs only on the `worker-transcode` service. Start with software
transcoding (`TRANSCODE_HWACCEL=none`) if the host is not configured for device
passthrough; the application reports the acceleration methods it actually
detects.

### NVIDIA

Install the NVIDIA driver and NVIDIA Container Toolkit on the host, then add the
following `deploy` section to `worker-transcode` in a Compose override:

```yaml
services:
  worker-transcode:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu, video]
```

Set `TRANSCODE_HWACCEL=nvenc` to require NVENC, or leave it at `auto` to allow
software fallback. Confirm the detected capability in the administration area
after restarting the worker.

### Intel and AMD (VAAPI or QSV)

Pass the render-device directory to `worker-transcode` and grant the container
access to the render group:

```yaml
services:
  worker-transcode:
    devices:
      - /dev/dri:/dev/dri
```

Use `TRANSCODE_HWACCEL=vaapi`, `qsv`, or `auto` as appropriate. The host driver,
render-device permissions, and FFmpeg encoder support must all be present; a
visible `/dev/dri` alone is not proof that transcoding works.

## Reverse proxy

Terminate TLS in a reverse proxy and send traffic to the published API port at
`127.0.0.1:8000`. Server-sent events must not be buffered.

```nginx
# nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

```caddyfile
# Caddy
media.example.test {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

For a sub-path deployment, set `BASE_PATH=/pornarr` in `.env` and configure the
proxy to preserve that prefix. Prefer a dedicated hostname when possible; it is
less error-prone.

## Upgrade

Read the release notes, create a backup, and pin the target image tag in `.env`:

```sh
$EDITOR .env
# Set PORNARR_TAG=<release-tag>
make backup
docker compose pull
docker compose up -d --wait
docker compose ps
```

The `migrate` service runs migrations before the API and workers start. Keep the
previous image and the backup until the API health check succeeds. The supported path
is forward to a newer published tag (including documented prereleases); downgrades
across database migrations are not supported. Restore the database backup instead.

## Troubleshooting

| Symptom | Check |
|---|---|
| Imports copy instead of hardlinking | Ensure every download and library path sits under the one `/data` mount. |
| Credentials fail after restore | Restore the same `APP_SECRET` that encrypted them. |
| Hardware transcoding uses CPU | Inspect detected capabilities, container device access, and the host driver; then use `auto` for software fallback. |
| Playback events stall behind a proxy | Disable buffering and set a long read timeout for the event stream. |
| Services do not start after an upgrade | Inspect `docker compose logs migrate` first; migrations must finish before API and workers start. |
| Health reports a stale or missing backup | Check `BACKUP_PATH_HOST`, run `make backup`, and set `BACKUP_MAX_AGE_HOURS` only when backups are scheduled. |

For deployment design and data-path rationale, see
[deployment.md](deployment.md).
