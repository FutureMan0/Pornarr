"""Recorded Torznab responses from different indexers."""

from __future__ import annotations

import httpx
import pytest

from pornarr_integrations.health import IndexerFailure
from pornarr_integrations.torznab import (
    TorznabAdapter,
    TorznabResponseError,
    parse_capabilities,
    parse_results,
)


def test_capabilities_and_torrent_attributes_parse_without_defaulting_missing_values() -> None:
    capabilities = parse_capabilities(
        '<caps><categories><category id="5000" name="TV" /></categories></caps>'
    )
    releases = parse_results(
        """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
        <title>Example 1080p</title><guid>one</guid><link>https://indexer.example/details/one</link>
        <pubDate>Sat, 09 Dec 2023 00:00:00 +0100</pubDate><enclosure url="https://indexer.example/download/one" length="1610612736" />
        <torznab:attr name="category" value="5000" /><torznab:attr name="seeders" value="18" />
        <torznab:attr name="peers" value="20" /><torznab:attr name="infohash" value="abc" />
        <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc" /></item></channel></rss>"""
    )

    assert capabilities[0].name == "TV"
    assert releases[0].seeders == 18
    assert releases[0].peers == 20
    assert releases[0].info_hash == "abc"
    assert releases[0].magnet_url == "magnet:?xt=urn:btih:abc"
    assert releases[0].size == 1610612736
    assert releases[0].categories == ("5000",)
    second = parse_results(
        """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
        <title>Other Indexer Result</title><guid>two</guid><torznab:attr name="category" value="5000" />
        <torznab:attr name="category" value="100001" /><torznab:attr name="peers" value="5" />
        </item></channel></rss>"""
    )[0]
    assert second.categories == ("5000", "100001")
    assert second.seeders is None
    assert second.peers == 5


def test_malformed_or_incomplete_results_raise_structured_errors() -> None:
    with pytest.raises(TorznabResponseError) as malformed:
        parse_results("not xml")
    with pytest.raises(TorznabResponseError) as incomplete:
        parse_results("<rss><channel><item><guid>one</guid></item></channel></rss>")

    assert malformed.value.code == "TORZNAB_RESPONSE_INVALID"
    assert incomplete.value.code == "TORZNAB_RESPONSE_INVALID"
    assert malformed.value.failure is IndexerFailure.MALFORMED_RESPONSE


async def test_authentication_response_is_not_classified_as_transient(monkeypatch) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(401, request=httpx.Request("GET", "https://indexer.example"))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: Client())

    with pytest.raises(TorznabResponseError) as error:
        await TorznabAdapter()._request("https://indexer.example", "key", {"t": "caps"})

    assert error.value.failure is IndexerFailure.AUTHENTICATION
    assert str(error.value) == "The Torznab authentication failed."
