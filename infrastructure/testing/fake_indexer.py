"""A Torznab indexer with exactly one release, for proving the grab path.

The acquisition chain - search an indexer, grab a release, hand it to a
download client, notice the completion, import the file - could never be run
end to end here, because the compose stack ships no indexer and no download
client. Every test of it was skipped, which is the same as not having tested
it.

This service is the indexer half. It generates one real, probeable media file,
builds a real torrent for it, and publishes it as a Torznab result whose magnet
carries that torrent's info hash.

The transfer itself cannot happen: a torrent needs a peer, and there is no
seeder on an isolated compose network. So this service plays the seeder's part
instead. It watches the client for the magnet Pornarr just handed it, and then
supplies what a peer would have supplied - the metadata and the data, which sit
ready in the client's incomplete directory. The client verifies them, completes
the torrent and moves the file into the download tree exactly as it would after
a real transfer.

Everything downstream of the grab is therefore real: the completion, the path
the client reports, the import trigger, the pipeline and the library record.
Nothing here belongs anywhere near a production deployment; it is started by an
extra compose file, never by the default stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from xml.sax.saxutils import escape

PORT = int(os.environ.get("FAKE_INDEXER_PORT", "9117"))
INCOMPLETE_PATH = Path(os.environ.get("FAKE_INDEXER_INCOMPLETE_PATH", "/data/qbt-incomplete"))
TITLE = os.environ.get("FAKE_INDEXER_TITLE", "Fake Studio - Compose Test Scene (2026) 1080p")
QBITTORRENT_URL = os.environ.get("FAKE_INDEXER_QBITTORRENT_URL", "http://qbittorrent:8080")
FIXTURE_SECONDS = int(os.environ.get("FAKE_INDEXER_SECONDS", "45"))
PIECE_LENGTH = 262_144
CLIENT_UID = int(os.environ.get("FAKE_INDEXER_UID", "1000"))
CLIENT_GID = int(os.environ.get("FAKE_INDEXER_GID", "1000"))
TRACKER = "http://fake-indexer:9117/announce"
QBITTORRENT_WAIT_SECONDS = 120
SEED_POLL_SECONDS = 1.0


def bencode(value: object) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = sorted(
            (key.encode() if isinstance(key, str) else key, item) for key, item in value.items()
        )
        return b"d" + b"".join(bencode(key) + bencode(item) for key, item in items) + b"e"
    raise TypeError(f"cannot bencode {type(value)!r}")


def write_fixture(path: Path) -> None:
    """A real H.264/AAC file, large enough to clear the import size floor."""

    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(path.parent, CLIENT_UID, CLIENT_GID)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1920x1080:rate=30:duration={FIXTURE_SECONDS}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={FIXTURE_SECONDS}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-qp",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    # The client has to be able to move it out of here when it completes.
    os.chown(path, CLIENT_UID, CLIENT_GID)


def build_torrent(path: Path) -> tuple[bytes, str, int]:
    """Return the bencoded torrent for one file, its info hash and its size.

    The size comes from the bytes read here rather than a second look at the
    file: the client moves the file out of the incomplete directory the moment
    it finishes with it, and a restart that lands in that gap should not crash.
    """

    data = path.read_bytes()
    pieces = b"".join(
        hashlib.sha1(data[offset : offset + PIECE_LENGTH]).digest()
        for offset in range(0, len(data), PIECE_LENGTH)
    )
    info = {
        "length": len(data),
        "name": path.name,
        "piece length": PIECE_LENGTH,
        "pieces": pieces,
    }
    info_hash = hashlib.sha1(bencode(info)).hexdigest()
    document = {
        "announce": TRACKER,
        "created by": "pornarr-fake-indexer",
        "creation date": int(time.time()),
        "info": info,
    }
    return bencode(document), info_hash, len(data)


def _multipart(fields: list[tuple[str, bytes]], torrent: bytes | None) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    if torrent is not None:
        parts.append(
            (
                b'Content-Disposition: form-data; name="torrents"; filename="fixture.torrent"\r\n'
                b"Content-Type: application/x-bittorrent\r\n\r\n",
                torrent,
            )
        )
    parts.extend(
        (b'Content-Disposition: form-data; name="%s"\r\n\r\n' % name.encode(), value)
        for name, value in fields
    )
    body = (
        b"".join(
            b"--%s\r\n%s%s\r\n" % (boundary.encode(), header, payload) for header, payload in parts
        )
        + b"--%s--\r\n" % boundary.encode()
    )
    return body, boundary


def _post(path: str, body: bytes, content_type: str) -> int | None:
    request = urllib.request.Request(
        f"{QBITTORRENT_URL}{path}", data=body, headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"fake-indexer: {path} failed ({error})", flush=True)
        return None


def _torrent_state(info_hash: str) -> dict[str, object] | None:
    url = f"{QBITTORRENT_URL}/api/v2/torrents/info?hashes={info_hash}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            torrents = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return torrents[0] if torrents else None


def seed_when_grabbed(torrent: bytes, info_hash: str) -> None:
    """Play the seeder: supply metadata and data once the client has the magnet.

    Pornarr grabs by magnet, which carries an info hash and nothing else, so a
    client with no peers sits waiting for metadata forever. Handing it the same
    torrent as a file supplies exactly what a peer would have, and the data is
    already in the incomplete directory, so the client's own verification is
    what completes the download.
    """

    supplied = False
    while True:
        time.sleep(SEED_POLL_SECONDS)
        state = _torrent_state(info_hash)
        if state is None:
            supplied = False
            continue
        if float(state.get("progress", 0)) >= 1:
            continue
        # A verification in flight is the client doing exactly what was asked;
        # asking again would only restart it, forever.
        if str(state.get("state", "")).startswith("checking"):
            continue
        if supplied:
            _post(
                "/api/v2/torrents/recheck",
                f"hashes={info_hash}".encode(),
                "application/x-www-form-urlencoded",
            )
            continue
        print(f"fake-indexer: seeding {info_hash} for a client that asked for it", flush=True)
        body, boundary = _multipart([("skip_checking", b"true")], torrent)
        _post("/api/v2/torrents/add", body, f"multipart/form-data; boundary={boundary}")
        supplied = True


CAPABILITIES = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="Pornarr fake indexer"/>
  <limits max="100" default="50"/>
  <searching>
    <search available="yes" supportedParams="q"/>
  </searching>
  <categories>
    <category id="6000" name="XXX">
      <subcat id="6010" name="XXX/DVD"/>
    </category>
  </categories>
</caps>
"""


class Handler(BaseHTTPRequestHandler):
    torrent: bytes = b""
    info_hash: str = ""
    size: int = 0

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.rstrip("/") in ("/api", ""):
            self._respond_torznab(query)
        elif parsed.path == "/torrent":
            self._respond(200, "application/x-bittorrent", self.torrent)
        else:
            self._respond(404, "text/plain", b"not found")

    def _respond_torznab(self, query: dict[str, list[str]]) -> None:
        kind = (query.get("t") or ["search"])[0]
        if kind == "caps":
            self._respond(200, "application/xml", CAPABILITIES.encode())
            return
        term = (query.get("q") or [""])[0]
        # One release, and it answers every query: a search that matches
        # nothing would prove nothing about the grab path.
        self._respond(200, "application/rss+xml", self._results(term).encode())

    def _results(self, term: str) -> str:
        magnet = (
            f"magnet:?xt=urn:btih:{self.info_hash}"
            f"&dn={urllib.parse.quote(TITLE)}&tr={urllib.parse.quote(TRACKER)}"
        )
        # Escaped, because a magnet is a query string and a bare & in XML is
        # what turns a working indexer into "malformed_response".
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Pornarr fake indexer</title>
    <item>
      <title>{escape(TITLE)}</title>
      <guid>fake-indexer-{self.info_hash}</guid>
      <link>http://fake-indexer:{PORT}/torrent</link>
      <comments>{escape(term)}</comments>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <enclosure url="http://fake-indexer:{PORT}/torrent" length="{self.size}"
                 type="application/x-bittorrent"/>
      <torznab:attr name="category" value="6010"/>
      <torznab:attr name="seeders" value="12"/>
      <torznab:attr name="peers" value="14"/>
      <torznab:attr name="infohash" value="{self.info_hash}"/>
      <torznab:attr name="magneturl" value="{escape(magnet)}"/>
    </item>
  </channel>
</rss>
"""

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - fixed signature
        print(f"fake-indexer: {format % args}", flush=True)


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("fake-indexer needs ffmpeg to generate its fixture")
    fixture = INCOMPLETE_PATH / f"{TITLE}.mp4"
    write_fixture(fixture)
    torrent, info_hash, size = build_torrent(fixture)
    Handler.torrent, Handler.info_hash, Handler.size = torrent, info_hash, size
    print(f"fake-indexer: serving {TITLE} ({info_hash})", flush=True)
    Thread(target=seed_when_grabbed, args=(torrent, info_hash), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
