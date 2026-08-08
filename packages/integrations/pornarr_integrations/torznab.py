"""Torznab capability discovery and torrent-result parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from pornarr_integrations.indexers import IndexerCategory, Release
from pornarr_shared.errors import PornarrError

REQUEST_TIMEOUT_SECONDS = 10


class TorznabResponseError(PornarrError):
    code = "TORZNAB_RESPONSE_INVALID"
    status = 502


class TorznabAdapter:
    async def test_connection(self, *, base_url: str, api_key: str) -> list[IndexerCategory]:
        return parse_capabilities(await self._request(base_url, api_key, {"t": "caps"}))

    async def search(self, *, base_url: str, api_key: str, query: str) -> list[Release]:
        return parse_results(await self._request(base_url, api_key, {"t": "search", "q": query}))

    async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    base_url.rstrip("/"), params={"apikey": api_key, **params}
                )
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as error:
            raise TorznabResponseError("The Torznab request failed.") from error


def parse_capabilities(document: str) -> list[IndexerCategory]:
    root = _root(document)
    return [
        IndexerCategory(category.attrib["id"], category.attrib["name"])
        for category in root.findall(".//category")
        if category.attrib.get("id") and category.attrib.get("name")
    ]


def parse_results(document: str) -> list[Release]:
    root = _root(document)
    releases: list[Release] = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        guid = item.findtext("guid")
        if not title or not guid:
            raise TorznabResponseError("A Torznab result is missing its title or guid.")
        attributes = [
            (attribute.attrib["name"], attribute.attrib["value"])
            for attribute in item
            if attribute.tag.endswith("attr")
            and attribute.attrib.get("name")
            and attribute.attrib.get("value") is not None
        ]
        attribute_values = dict(attributes)
        enclosure = item.find("enclosure")
        releases.append(
            Release(
                guid=guid,
                title=title,
                details_url=item.findtext("link"),
                download_url=enclosure.attrib.get("url") if enclosure is not None else None,
                published_at=_date(item.findtext("pubDate")),
                size=_integer(enclosure.attrib.get("length") if enclosure is not None else None),
                categories=tuple(value for name, value in attributes if name == "category"),
                seeders=_integer(attribute_values.get("seeders")),
                peers=_integer(attribute_values.get("peers")),
                info_hash=attribute_values.get("infohash"),
                magnet_url=attribute_values.get("magneturl"),
            )
        )
    return releases


def _root(document: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise TorznabResponseError("The Torznab response is malformed XML.") from error


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return date.replace(tzinfo=UTC) if date.tzinfo is None else date
