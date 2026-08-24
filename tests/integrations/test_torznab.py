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


async def test_adapter_requests_capabilities_and_search_results(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
            calls.append((url, params))
            document = (
                '<caps><categories><category id="5000" name="TV" /></categories></caps>'
                if params["t"] == "caps"
                else "<rss><channel><item><title>Example</title><guid>one</guid></item></channel></rss>"
            )
            return httpx.Response(200, text=document, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: Client())
    adapter = TorznabAdapter()

    assert (await adapter.test_connection(base_url="https://indexer.example/", api_key="key"))[
        0
    ].id == "5000"
    assert (
        await adapter.search(base_url="https://indexer.example/", api_key="key", query="example")
    )[0].guid == "one"
    assert calls == [
        ("https://indexer.example", {"apikey": "key", "t": "caps"}),
        ("https://indexer.example", {"apikey": "key", "t": "search", "q": "example"}),
    ]


@pytest.mark.parametrize(
    ("result", "expected_failure", "expected_message"),
    [
        (
            httpx.Response(500, request=httpx.Request("GET", "https://indexer.example")),
            IndexerFailure.TRANSIENT,
            "The Torznab request failed.",
        ),
        (
            httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://indexer.example")),
            IndexerFailure.TIMEOUT,
            "The Torznab request timed out.",
        ),
        (
            httpx.ConnectError("offline", request=httpx.Request("GET", "https://indexer.example")),
            IndexerFailure.TRANSIENT,
            "The Torznab request failed.",
        ),
    ],
)
async def test_transport_failures_are_structured(
    monkeypatch,
    result: httpx.Response | httpx.HTTPError,
    expected_failure: IndexerFailure,
    expected_message: str,
) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *args: object, **kwargs: object) -> httpx.Response:
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: Client())

    with pytest.raises(TorznabResponseError) as error:
        await TorznabAdapter()._request("https://indexer.example", "key", {"t": "caps"})

    assert error.value.failure is expected_failure
    assert str(error.value) == expected_message


def test_optional_torznab_fields_tolerate_invalid_values() -> None:
    release = parse_results(
        """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
        <title>Example</title><guid>one</guid><pubDate>not a date</pubDate>
        <enclosure length="not-a-number" /><torznab:attr name="parts" value="invalid" />
        <torznab:attr name="group" value="alt.example" /><torznab:attr name="poster" value="poster" />
        <torznab:attr name="password" value="yes" /></item></channel></rss>"""
    )[0]
    unprotected = parse_results(
        """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
        <title>Other</title><guid>two</guid><torznab:attr name="password" value="no" />
        </item></channel></rss>"""
    )[0]
    unknown = parse_results(
        """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
        <title>Unknown</title><guid>three</guid><torznab:attr name="password" value="maybe" />
        </item></channel></rss>"""
    )[0]

    assert (release.published_at, release.size, release.parts) == (None, None, None)
    assert (release.groups, release.poster, release.password_protected) == (
        ("alt.example",),
        "poster",
        True,
    )
    assert unprotected.password_protected is False
    assert unknown.password_protected is None


async def test_rss_request_has_no_search_term() -> None:
    class RecordedAdapter(TorznabAdapter):
        def __init__(self) -> None:
            self.params: dict[str, str] | None = None

        async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
            self.params = params
            return (
                "<rss><channel><item><title>Example</title><guid>one</guid></item></channel></rss>"
            )

    adapter = RecordedAdapter()
    releases = await adapter.rss(base_url="https://indexer.example/api", api_key="secret")

    assert releases[0].guid == "one"
    assert adapter.params == {"t": "search"}


async def test_search_and_rss_restrict_requests_to_the_configured_categories() -> None:
    class RecordedAdapter(TorznabAdapter):
        def __init__(self) -> None:
            self.params: dict[str, str] | None = None

        async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
            self.params = params
            return (
                "<rss><channel><item><title>Example</title><guid>one</guid></item></channel></rss>"
            )

    adapter = RecordedAdapter()
    await adapter.search(
        base_url="https://indexer.example/api",
        api_key="secret",
        query="example",
        categories=("6000", "6010"),
    )
    assert adapter.params == {"t": "search", "q": "example", "cat": "6000,6010"}

    await adapter.rss(
        base_url="https://indexer.example/api", api_key="secret", categories=("6000",)
    )
    assert adapter.params == {"t": "search", "cat": "6000"}

    await adapter.search(base_url="https://indexer.example/api", api_key="secret", query="example")
    assert adapter.params == {"t": "search", "q": "example"}
