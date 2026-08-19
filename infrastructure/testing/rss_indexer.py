"""A Torznab indexer with a feed that can be changed between polls.

ADR 0030's RSS claims cannot be run against either indexer the stack already
has. `fake_indexer.py` publishes one release whose GUID never changes, so the
first `rss_sync` records it as `last_rss_guid` and every later poll correctly
sees nothing new - which makes "matches new releases against all monitors" and
"creates automatic requests at priority 60" unreachable forever after.
`control_indexer.py` answers a feed request (a search with no `q`) with an empty
channel by design, so it has no feed at all.

This service is the missing half, and it is deliberately the only one of the
three that a test can *write* to:

- `POST/GET /publish?title=...` prepends one item with a fresh GUID, so a poll
  after it has genuinely new entries and a poll after that has none.
- `GET /stats` reports how many feed requests and how many term searches it has
  answered. ADR 0030 L11 says RSS costs one request per indexer per cycle
  regardless of how many monitors exist; that is a claim about requests arriving
  here, and counting them here is the only way to see it rather than infer it.
- `GET /reset` empties the feed and the counters.

A term search (`q` present) answers with the matching published items only, so
while this indexer is registered it adds nothing to another suite's search
results unless that search names one of this suite's own titles.

Started on demand by the end-to-end suite (`docker exec` inside the
`fake-indexer` container), never by the default stack, and useless anywhere near
a real deployment.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

PORT = int(os.environ.get("RSS_INDEXER_PORT", "9119"))
HOST_NAME = os.environ.get("RSS_INDEXER_HOST", "fake-indexer")
# Never transferred, only reported: nothing here is grabbable on purpose, so a
# monitor match cannot turn into a download that outlives the test.
SIZE_BYTES = int(os.environ.get("RSS_INDEXER_SIZE", str(3 * 1024**3)))

CAPABILITIES = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="Pornarr RSS indexer"/>
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


class Feed:
    """Newest first, because that is the order `new_releases` walks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, str]] = []
        self.feed_requests = 0
        self.search_requests = 0

    def publish(self, title: str) -> str:
        guid = f"pornarr-rss-{hashlib.sha1(title.encode()).hexdigest()}"
        with self._lock:
            self._items = [(guid, title), *(item for item in self._items if item[0] != guid)]
        return guid

    def reset(self) -> None:
        with self._lock:
            self._items = []
            self.feed_requests = 0
            self.search_requests = 0

    def take(self, term: str) -> list[tuple[str, str]]:
        with self._lock:
            if not term:
                self.feed_requests += 1
                return list(self._items)
            self.search_requests += 1
            folded = term.casefold()
            return [item for item in self._items if folded in item[1].casefold()]

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {
                "feed_requests": self.feed_requests,
                "search_requests": self.search_requests,
                "items": len(self._items),
            }


FEED = Feed()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        if path == "/publish":
            title = (query.get("title") or [""])[0].strip()
            if not title:
                self._respond(400, "text/plain", b"title is required")
                return
            self._respond(
                200, "application/json", json.dumps({"guid": FEED.publish(title)}).encode()
            )
            return
        if path == "/reset":
            FEED.reset()
            self._respond(200, "application/json", b'{"reset": true}')
            return
        if path == "/stats":
            self._respond(200, "application/json", json.dumps(FEED.counters()).encode())
            return
        if path not in ("/api", ""):
            self._respond(404, "text/plain", b"not found")
            return
        if (query.get("t") or ["search"])[0] == "caps":
            self._respond(200, "application/xml", CAPABILITIES.encode())
            return
        term = (query.get("q") or [""])[0].strip()
        self._respond(200, "application/rss+xml", self._results(FEED.take(term)).encode())

    def _results(self, items: list[tuple[str, str]]) -> str:
        entries = "".join(self._item(guid, title) for guid, title in items)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Pornarr RSS indexer</title>
{entries}  </channel>
</rss>
"""

    def _item(self, guid: str, title: str) -> str:
        info_hash = guid.removeprefix("pornarr-rss-")
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(title)}"
        return f"""    <item>
      <title>{escape(title)}</title>
      <guid>{escape(guid)}</guid>
      <link>http://{HOST_NAME}:{PORT}/torrent</link>
      <pubDate>{formatdate(usegmt=True)}</pubDate>
      <enclosure url="http://{HOST_NAME}:{PORT}/torrent" length="{SIZE_BYTES}"
                 type="application/x-bittorrent"/>
      <torznab:attr name="category" value="6010"/>
      <torznab:attr name="seeders" value="5"/>
      <torznab:attr name="peers" value="6"/>
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

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
