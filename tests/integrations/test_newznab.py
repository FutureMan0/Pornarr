"""Recorded Newznab response parsing and download URL handling."""

from __future__ import annotations

from pornarr_integrations.newznab import NewznabAdapter, parse_results, with_api_key
from pornarr_integrations.torznab import parse_results as shared_parse_results


def test_newznab_reuses_the_shared_parser_and_keeps_usenet_fields() -> None:
    release = parse_results(
        """<rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><item>
        <title>Example Usenet Release</title><guid>one</guid><enclosure url="https://indexer.example/get/one" length="42" />
        <newznab:attr name="group" value="alt.binaries.example" /><newznab:attr name="poster" value="poster@example" />
        <newznab:attr name="parts" value="12" /><newznab:attr name="password" value="1" /></item></channel></rss>"""
    )[0]

    assert parse_results is shared_parse_results
    assert release.groups == ("alt.binaries.example",)
    assert release.poster == "poster@example"
    assert release.parts == 12
    assert release.password_protected is True


def test_download_urls_receive_one_api_key() -> None:
    assert (
        with_api_key("https://indexer.example/get/one", "secret")
        == "https://indexer.example/get/one?apikey=secret"
    )
    assert (
        with_api_key("https://indexer.example/get/one?apikey=present", "secret")
        == "https://indexer.example/get/one?apikey=present"
    )


async def test_newznab_search_injects_its_key_into_downloads() -> None:
    class RecordedAdapter(NewznabAdapter):
        async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
            return '<rss><channel><item><title>Example</title><guid>one</guid><enclosure url="https://indexer.example/get" /></item></channel></rss>'

    releases = await RecordedAdapter().search(
        base_url="https://indexer.example/api", api_key="secret", query="example"
    )

    assert releases[0].download_url == "https://indexer.example/get?apikey=secret"


async def test_newznab_rss_injects_its_key_into_downloads() -> None:
    class RecordedAdapter(NewznabAdapter):
        async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
            return '<rss><channel><item><title>Example</title><guid>one</guid><enclosure url="https://indexer.example/get" /></item></channel></rss>'

    releases = await RecordedAdapter().rss(base_url="https://indexer.example/api", api_key="secret")

    assert releases[0].download_url == "https://indexer.example/get?apikey=secret"


async def test_newznab_search_and_rss_restrict_requests_to_the_configured_categories() -> None:
    class RecordedAdapter(NewznabAdapter):
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
        categories=("6000",),
    )
    assert adapter.params == {"t": "search", "q": "example", "cat": "6000"}

    await adapter.rss(
        base_url="https://indexer.example/api", api_key="secret", categories=("6000",)
    )
    assert adapter.params == {"t": "search", "cat": "6000"}
