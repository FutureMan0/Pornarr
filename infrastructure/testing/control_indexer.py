"""A Torznab indexer whose releases can never finish downloading.

`fake_indexer.py` publishes one release and then plays the seeder for it, so
everything it hands to the download client completes within seconds and is
imported. That is exactly what the import pipeline needs and exactly what the
request lifecycle cannot use: pausing, resuming, re-prioritising and cancelling
a download are only observable while the download is still running, and
cancelling deletes the client's files - which, for the one seeded fixture, are
the very bytes the rest of the suite depends on.

This service is the other half. It publishes a distinct release per search term
(`info_hash = sha1(term)`) whose magnet names a swarm that does not exist. The
compose client has DHT, PeX and local discovery switched off, so a torrent
added from one of these magnets sits in `metaDL` for as long as it is left
there: real submission, real client-side state, no transfer and no files. A
cancelled one deletes nothing, and every term yields a fresh release, so a
lifecycle test does not have to share one job with every other test.

Started on demand by the end-to-end suite (`docker exec` inside the
`fake-indexer` container), never by the default stack, and useless anywhere
near a real deployment.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

PORT = int(os.environ.get("CONTROL_INDEXER_PORT", "9118"))
HOST_NAME = os.environ.get("CONTROL_INDEXER_HOST", "fake-indexer")
# Large enough to look like a real release and to keep a percentage readable;
# nothing ever transfers it, so the figure is only ever reported, never moved.
SIZE_BYTES = int(os.environ.get("CONTROL_INDEXER_SIZE", str(4 * 1024**3)))
TRACKER = f"http://{HOST_NAME}:{PORT}/announce"
# The term that proves the empty case: an indexer answering a search with no
# results at all, which the seeded fixture indexer can never do.
NOTHING = "gauntlet-control-nothing"

CAPABILITIES = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="Pornarr control indexer"/>
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


def info_hash_for(term: str) -> str:
    """One stable, unique info hash per search term."""
    return hashlib.sha1(term.encode()).hexdigest()


def title_for(term: str) -> str:
    return f"Gauntlet Control {term} (2026) 2160p"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.rstrip("/") not in ("/api", ""):
            self._respond(404, "text/plain", b"not found")
            return
        if (query.get("t") or ["search"])[0] == "caps":
            self._respond(200, "application/xml", CAPABILITIES.encode())
            return
        term = (query.get("q") or [""])[0].strip()
        self._respond(200, "application/rss+xml", self._results(term).encode())

    def _results(self, term: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Pornarr control indexer</title>
{"" if not term or term == NOTHING else self._item(term)}  </channel>
</rss>
"""

    def _item(self, term: str) -> str:
        info_hash = info_hash_for(term)
        title = title_for(term)
        magnet = (
            f"magnet:?xt=urn:btih:{info_hash}"
            f"&dn={urllib.parse.quote(title)}&tr={urllib.parse.quote(TRACKER)}"
        )
        # Escaped: a bare & in XML is what turns a working indexer into
        # `malformed_response`.
        return f"""    <item>
      <title>{escape(title)}</title>
      <guid>gauntlet-control-{info_hash}</guid>
      <link>http://{HOST_NAME}:{PORT}/torrent</link>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <enclosure url="http://{HOST_NAME}:{PORT}/torrent" length="{SIZE_BYTES}"
                 type="application/x-bittorrent"/>
      <torznab:attr name="category" value="6010"/>
      <torznab:attr name="seeders" value="7"/>
      <torznab:attr name="peers" value="9"/>
      <torznab:attr name="infohash" value="{info_hash}"/>
      <torznab:attr name="magneturl" value="{escape(magnet)}"/>
    </item>
"""

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - fixed signature
        print(f"control-indexer: {format % args}", flush=True)


def main() -> None:
    print(f"control-indexer: serving unfinishable releases on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
