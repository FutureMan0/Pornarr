"""ThePornDB REST metadata adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import httpx

from pornarr_integrations.metadata import MetadataCandidate, MetadataRateLimitError
from pornarr_shared.errors import PornarrError

DEFAULT_ENDPOINT = "https://api.theporndb.net"
REQUEST_TIMEOUT_SECONDS = 10
RESULTS_PER_PAGE = 25


class TpdbResponseError(PornarrError):
    """ThePornDB did not return a usable REST response."""

    code = "TPDB_RESPONSE_INVALID"
    status = 502


class TpdbAdapter:
    """Map ThePornDB's REST scenes endpoint onto the metadata provider contract."""

    name = "tpdb"
    precedence = 1

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    @classmethod
    def from_configuration(
        cls,
        *,
        endpoint: str | None = None,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> TpdbAdapter | None:
        """Return no adapter when the encrypted provider key is not configured."""
        if not api_key:
            return None
        return cls(endpoint=endpoint or DEFAULT_ENDPOINT, api_key=api_key, transport=transport)

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        """StashDB is the fingerprint authority in Pornarr's provider cascade."""
        del oshash, perceptual_hash
        return None

    async def find_by_site_date_title(
        self, *, site: str, release_date: date, title: str
    ) -> MetadataCandidate | None:
        candidates = await self._search_scenes(
            {
                "site": site,
                "date": release_date.isoformat(),
                "date_operation": "=",
                "title": title,
                "per_page": RESULTS_PER_PAGE,
            }
        )
        matches = [
            candidate
            for candidate in candidates
            if candidate.site is not None
            and candidate.site.casefold() == site.casefold()
            and candidate.release_date == release_date
            and candidate.title.casefold() == title.casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    async def search(self, *, title: str, performers: tuple[str, ...]) -> list[MetadataCandidate]:
        del performers  # The cascade validates performer overlap across all results.
        return await self._search_scenes({"title": title, "per_page": RESULTS_PER_PAGE})

    async def _search_scenes(self, params: dict[str, str | int]) -> list[MetadataCandidate]:
        payload = await self._request("/scenes", params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise TpdbResponseError("ThePornDB returned an invalid scenes response.")
        return _candidates(data)

    async def _request(self, path: str, params: dict[str, str | int]) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.get(f"{self._endpoint}{path}", params=params)
        except httpx.TimeoutException as error:
            raise TpdbResponseError("ThePornDB request timed out.") from error
        except httpx.HTTPError as error:
            raise TpdbResponseError("ThePornDB request failed.") from error

        if response.status_code == 429:
            raise MetadataRateLimitError(_retry_after(response))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise TpdbResponseError("ThePornDB request failed.") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise TpdbResponseError("ThePornDB returned invalid JSON.") from error
        if not isinstance(payload, Mapping):
            raise TpdbResponseError("ThePornDB returned an invalid response.")
        return payload


def _candidates(scenes: list[object]) -> list[MetadataCandidate]:
    return [candidate for scene in scenes if (candidate := _candidate(scene)) is not None]


def _candidate(scene: object) -> MetadataCandidate | None:
    if (
        not isinstance(scene, Mapping)
        or not isinstance(title := scene.get("title"), str)
        or not title
    ):
        return None
    site = scene.get("site")
    site_name = site.get("name") if isinstance(site, Mapping) else None
    site_id = _identifier(site) if isinstance(site, Mapping) else None
    performers, performer_ids = _named_entities(scene.get("performers"))
    tags, tag_ids = _named_entities(scene.get("tags"))
    provider_id = _identifier(scene)
    raw: dict[str, object] = {
        "scene_id": provider_id,
        "site_id": site_id,
        "performer_ids": performer_ids,
        "tag_ids": tag_ids,
    }
    return MetadataCandidate(
        title=title,
        site=site_name if isinstance(site_name, str) else None,
        release_date=_release_date(scene.get("date")),
        performers=performers,
        studio=site_name if isinstance(site_name, str) else None,
        tags=tags,
        provider_id=provider_id,
        raw=raw,
    )


def _named_entities(values: object) -> tuple[tuple[str, ...], list[str]]:
    if not isinstance(values, list):
        return (), []
    names: list[str] = []
    identifiers: list[str] = []
    seen: set[str] = set()
    for entity in values:
        if not isinstance(entity, Mapping) or not isinstance(name := entity.get("name"), str):
            continue
        identifier = _identifier(entity)
        identity = identifier or name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        names.append(name)
        if identifier:
            identifiers.append(identifier)
    return tuple(names), identifiers


def _identifier(value: Mapping[str, object]) -> str | None:
    for key in ("uuid", "id", "_id"):
        identifier = value.get(key)
        if isinstance(identifier, (str, int)):
            return str(identifier)
    return None


def _release_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("Retry-After", "60"))
    except ValueError:
        return 60
