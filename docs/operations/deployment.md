# Deployment

## Requirements

- Docker Engine with the Compose plugin
- One filesystem holding downloads and the library (see below)
- For hardware transcoding: a supported GPU and, for NVIDIA, the NVIDIA Container
  Toolkit

## The one-mount rule

This is the single most common self-hosting mistake, so it is stated first.

Downloads and the library must be on the **same filesystem**, mounted into the
container under one path:

```
/data
  /torrents/{category}/    qBittorrent writes here
  /usenet/{category}/      SABnzbd writes here
  /library/...             Pornarr manages this
  /quarantine/
  /thumbnails/
  /transcodes/
```

Import then creates hardlinks: instant, no extra disk usage, and the torrent keeps
seeding. Mounting `/downloads` and `/library` as two separate volumes puts them on
different filesystems inside the container, hardlinking fails, and every import
silently degrades to a full copy.

Pornarr compares the device identifiers of the configured paths at startup and warns
loudly when they differ. Do not ignore that warning.

## Compose

Four Pornarr roles from one image, plus PostgreSQL and Redis:

```
migrate   runs once, exits, everything else waits for it
api       serves the API and the web UI
worker    background jobs; this is the role that needs the GPU
beat      cron scheduler
```

## GPU

For NVIDIA, install the container toolkit on the host and give the worker access to
the device. For Intel and AMD, pass through the render device and ensure the
container user is in the render group. The administration area reports which
acceleration methods were actually detected, which is the fastest way to confirm the
passthrough worked.

## Reverse proxy

The event stream must not be buffered.

```
# nginx
proxy_buffering off;
proxy_read_timeout 3600s;
```

```
# Caddy
reverse_proxy pornarr:8000 {
    flush_interval -1
}
```

Sub-path deployment is supported through the configured base path.

Two settings exist because of the proxy, and both matter:

- `SESSION_COOKIE_SECURE` defaults to `true`, so the session and CSRF cookies are
  never sent over a plain HTTP connection. Terminate TLS in the proxy and leave it
  alone. The opt-out is for local development over `http://localhost`, where a
  browser will not store a `Secure` cookie at all; it is refused outright when
  `APP_ENV=production`, so a production instance cannot be run without it.
- `TRUSTED_PROXIES` names the addresses whose `X-Forwarded-For` this instance may
  believe, comma-separated, as addresses or CIDR blocks. It is empty by default,
  which means the login rate limiter sees the proxy as the source of every request
  and counts the whole instance in one bucket: six wrong guesses from one
  anonymous caller then refuse every account's sign-in for fifteen minutes. Set it
  to the address the proxy reaches the API from. Name only addresses you control —
  any caller can write that header, and an instance that believes it from an
  arbitrary client has no login rate limit at all.

## Configuration

Only bootstrap values live in the environment: the secret key, database and Redis
URLs, and the data paths. Indexers, download clients, metadata providers, quality
profiles and filters are all configured in the UI and stored encrypted in the
database.
