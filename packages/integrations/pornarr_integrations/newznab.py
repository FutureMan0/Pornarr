"""Newznab search built on the shared Torznab-derived RSS parser."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pornarr_integrations.indexers import IndexerCategory, Release
from pornarr_integrations.torznab import TorznabAdapter, parse_capabilities, parse_results


class NewznabAdapter(TorznabAdapter):
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        return parse_capabilities(await self._request(base_url, api_key, {"t": "caps"}))

    async def search(
        self, *, base_url: str, api_key: str, query: str, categories: tuple[str, ...] = ()
    ) -> list[Release]:
        params = {"t": "search", "q": query}
        if categories:
            params["cat"] = ",".join(categories)
        releases = parse_results(await self._request(base_url, api_key, params))
        return self._with_api_key(releases, api_key)

    async def rss(
        self, *, base_url: str, api_key: str, categories: tuple[str, ...] = ()
    ) -> list[Release]:
        releases = await super().rss(base_url=base_url, api_key=api_key, categories=categories)
        return self._with_api_key(releases, api_key)

    @staticmethod
    def _with_api_key(releases: list[Release], api_key: str) -> list[Release]:
        return [
            replace(release, download_url=with_api_key(release.download_url, api_key))
            for release in releases
        ]


def with_api_key(url: str | None, api_key: str) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(name == "apikey" for name, _ in parameters):
        parameters.append(("apikey", api_key))
    return urlunsplit(parsed._replace(query=urlencode(parameters)))
