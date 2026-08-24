"""StashDB's GraphQL metadata adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import httpx

from pornarr_integrations.metadata import (
    MetadataCandidate,
    MetadataRateLimitError,
    site_key,
)
from pornarr_shared.errors import PornarrError

REQUEST_TIMEOUT_SECONDS = 10

FINGERPRINT_QUERY = """
query FindScenesBySceneFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {
  findScenesBySceneFingerprints(fingerprints: $fingerprints) {
    id
    title
    release_date
    studio { id name }
    performers { performer { id name } }
    tags { id name }
  }
}
"""

STUDIOS_QUERY = """
query QueryStudios($input: StudioQueryInput!) {
  queryStudios(input: $input) { studios { id name } }
}
"""

SCENES_QUERY = """
query QueryScenes($input: SceneQueryInput!) {
  queryScenes(input: $input) {
    scenes {
      id
      title
      release_date
      studio { id name }
      performers { performer { id name } }
      tags { id name }
    }
  }
}
"""


class StashdbResponseError(PornarrError):
    """StashDB did not return a usable GraphQL response."""

    code = "STASHDB_RESPONSE_INVALID"
    status = 502


class StashdbAdapter:
    """Map Stash-box's public GraphQL schema onto the metadata provider contract."""

    name = "stashdb"
    precedence = 0

    def __init__(
        self,
        *,
        endpoint: str,
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
        endpoint: str | None,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> StashdbAdapter | None:
        """Return no adapter when the encrypted provider key is not configured.

        The caller reads ``api_key`` from encrypted provider configuration. Keeping
        the optionality here means an empty configuration skips StashDB entirely
        instead of failing every import.
        """
        if not endpoint or not api_key:
            return None
        return cls(endpoint=endpoint, api_key=api_key, transport=transport)

    async def find_by_fingerprint(
        self, *, oshash: str | None, perceptual_hash: str | None
    ) -> MetadataCandidate | None:
        fingerprints = []
        if oshash:
            fingerprints.append({"algorithm": "OSHASH", "hash": oshash})
        if perceptual_hash:
            fingerprints.append({"algorithm": "PHASH", "hash": perceptual_hash})
        if not fingerprints:
            return None

        payload = await self._graphql(FINGERPRINT_QUERY, {"fingerprints": [fingerprints]})
        matches = _nested_scenes(payload, "findScenesBySceneFingerprints")
        candidates = _candidates(matches)
        return candidates[0] if len(candidates) == 1 else None

    async def find_by_site_date_title(
        self, *, site: str, release_date: date, title: str
    ) -> MetadataCandidate | None:
        """Find the exact scene using StashDB's studio, date and title fields.

        The provider-neutral interface calls this field ``site``; StashDB's
        corresponding canonical source is the studio name.
        """
        studio_payload = await self._graphql(
            STUDIOS_QUERY, {"input": {"name": site, "per_page": 25}}
        )
        studio_ids = _matching_studio_ids(studio_payload, site)
        if not studio_ids:
            return None

        scenes_payload = await self._graphql(
            SCENES_QUERY,
            {
                "input": {
                    "title": title,
                    "date": {"value": release_date.isoformat(), "modifier": "EQUALS"},
                    "studios": {"value": studio_ids, "modifier": "INCLUDES"},
                    "per_page": 25,
                }
            },
        )
        candidates = [
            candidate
            for candidate in _candidates(_scenes(scenes_payload))
            if candidate.release_date == release_date
            and candidate.studio is not None
            and site_key(candidate.studio) == site_key(site)
            and candidate.title.casefold() == title.casefold()
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def search(self, *, title: str, performers: tuple[str, ...]) -> list[MetadataCandidate]:
        del performers  # The cascade validates performer overlap across all results.
        payload = await self._graphql(SCENES_QUERY, {"input": {"title": title, "per_page": 25}})
        return _candidates(_scenes(payload))

    async def _graphql(self, query: str, variables: dict[str, object]) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                headers={"ApiKey": self._api_key},
            ) as client:
                response = await client.post(
                    self._endpoint, json={"query": query, "variables": variables}
                )
        except httpx.TimeoutException as error:
            raise StashdbResponseError("The StashDB request timed out.") from error
        except httpx.HTTPError as error:
            raise StashdbResponseError("The StashDB request failed.") from error

        if response.status_code == 429:
            raise MetadataRateLimitError(_retry_after(response))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise StashdbResponseError("The StashDB request failed.") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise StashdbResponseError("StashDB returned invalid JSON.") from error
        if (
            not isinstance(payload, Mapping)
            or payload.get("errors")
            or not isinstance(payload.get("data"), Mapping)
        ):
            raise StashdbResponseError("StashDB returned an invalid GraphQL response.")
        return payload["data"]


def _nested_scenes(payload: Mapping[str, object], field: str) -> list[object]:
    values = payload.get(field)
    if not isinstance(values, list) or not values:
        return []
    first = values[0]
    return first if isinstance(first, list) else []


def _scenes(payload: Mapping[str, object]) -> list[object]:
    result = payload.get("queryScenes")
    if not isinstance(result, Mapping):
        return []
    scenes = result.get("scenes")
    return scenes if isinstance(scenes, list) else []


def _matching_studio_ids(payload: Mapping[str, object], name: str) -> list[str]:
    result = payload.get("queryStudios")
    if not isinstance(result, Mapping) or not isinstance(result.get("studios"), list):
        return []
    return [
        identifier
        for studio in result["studios"]
        if isinstance(studio, Mapping)
        and isinstance(identifier := studio.get("id"), str)
        and isinstance(studio_name := studio.get("name"), str)
        and studio_name.casefold() == name.casefold()
    ]


def _candidates(scenes: list[object]) -> list[MetadataCandidate]:
    return [candidate for scene in scenes if (candidate := _candidate(scene)) is not None]


def _candidate(scene: object) -> MetadataCandidate | None:
    if (
        not isinstance(scene, Mapping)
        or not isinstance(title := scene.get("title"), str)
        or not title
    ):
        return None
    studio = scene.get("studio")
    studio_name = studio.get("name") if isinstance(studio, Mapping) else None
    studio_id = studio.get("id") if isinstance(studio, Mapping) else None
    performers, performer_ids = _named_entities(scene.get("performers"), nested_key="performer")
    tags, tag_ids = _named_entities(scene.get("tags"))
    raw: dict[str, object] = {
        "scene_id": scene.get("id"),
        "studio_id": studio_id,
        "performer_ids": performer_ids,
        "tag_ids": tag_ids,
    }
    return MetadataCandidate(
        title=title,
        site=studio_name if isinstance(studio_name, str) else None,
        release_date=_release_date(scene.get("release_date")),
        performers=performers,
        studio=studio_name if isinstance(studio_name, str) else None,
        tags=tags,
        provider_id=scene.get("id") if isinstance(scene.get("id"), str) else None,
        raw=raw,
    )


def _named_entities(
    values: object, *, nested_key: str | None = None
) -> tuple[tuple[str, ...], list[str]]:
    if not isinstance(values, list):
        return (), []
    names: list[str] = []
    identifiers: list[str] = []
    seen: set[str] = set()
    for value in values:
        entity = value.get(nested_key) if nested_key and isinstance(value, Mapping) else value
        if not isinstance(entity, Mapping) or not isinstance(name := entity.get("name"), str):
            continue
        identifier = entity.get("id")
        identity = identifier if isinstance(identifier, str) else name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        names.append(name)
        if isinstance(identifier, str):
            identifiers.append(identifier)
    return tuple(names), identifiers


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
