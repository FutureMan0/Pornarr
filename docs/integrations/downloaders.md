# Download clients

qBittorrent and SABnzbd ship in v1. Clients are rows in `download_clients`; multiple
instances per protocol are supported.

```
id, name, protocol, implementation, host, port, url_base,
username, password_encrypted, api_key_encrypted, category,
priority, remove_completed, enabled, health_status
```

A grab is routed by protocol first, then priority, then health.

## Status mapping

The most error-prone part of the integration. Each client has its own vocabulary, and
both are mapped onto one internal lifecycle:

```
queued -> downloading -> completed -> importing -> imported
                      -> failed
                      -> cancelled
```

qBittorrent reports states such as `stalledDL`, `metaDL`, `checkingDL` and `pausedDL`
that all mean different things for progress and for estimation. SABnzbd distinguishes
downloading from extracting from repairing, and only the first contributes to the
download estimate.

Mapping is covered by tests against recorded responses from real clients, not
hand-written mocks, so a fixture drifting from reality is visible.

## Estimation

The client's own estimate wins whenever it provides one; it knows its seeders,
connections and internal queue. Our fallback is remaining bytes over a sixty-second
moving average. See [pipelines/estimation](../pipelines/import.md) and
[ADR-0031](../adr/0031-estimation.md).

## Removal

`remove_completed` controls whether a finished job is removed from the client after a
successful import. For torrents this must respect seeding: the job is removed only
once the configured ratio or seeding time is met, never immediately after import.
