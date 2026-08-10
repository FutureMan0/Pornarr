# Download-client recordings

Fixtures are captured from real services so adapter tests exercise their actual
wire shapes rather than reconstructed mocks. They contain no credentials, cookies,
or private URLs.

## qBittorrent

`fixtures/qbittorrent-torrents-info.json` was captured on 2026-08-08 from
qBittorrent v5.2.3 (linuxserver.io image `5.2.3_v2.0.13-ls469`) after adding a
paused, inert magnet under the `pornarr` category. It records the
`/api/v2/torrents/info?category=pornarr` response.

To refresh it, start a disposable qBittorrent v5 instance, authenticate against
its WebUI, add a harmless paused magnet, fetch that endpoint, and inspect the
recording before replacing this fixture.
