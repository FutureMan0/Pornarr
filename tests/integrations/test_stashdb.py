"""StashDB GraphQL adapter behaviour against recorded protocol responses."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from pornarr_integrations.metadata import MetadataRateLimitError
from pornarr_integrations.metadata.stashdb import StashdbAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


async def test_fingerprint_lookup_maps_the_known_scene_and_deduplicates_entities() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=_fixture("stashdb-fingerprint.json"))

    adapter = StashdbAdapter.from_configuration(
        endpoint="https://stashdb.example/graphql",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    candidate = await adapter.find_by_fingerprint(
        oshash="0123456789abcdef", perceptual_hash="fedcba9876543210"
    )

    assert candidate is not None
    assert candidate.provider_id == "scene-known-123"
    assert candidate.title == "Known Scene"
    assert candidate.release_date == date(2024, 2, 14)
    assert candidate.site == candidate.studio == "Example Studio"
    assert candidate.performers == ("Alice Example", "Bob Example")
    assert candidate.tags == ("Outdoor", "HD")
    assert candidate.raw["performer_ids"] == ["performer-7", "performer-8"]
    assert candidate.raw["studio_id"] == "studio-42"
    assert candidate.raw["tag_ids"] == ["tag-2", "tag-3"]

    assert requests[0].url == "https://stashdb.example/graphql"
    assert requests[0].headers["ApiKey"] == "fixture-key"
    payload = json.loads(requests[0].content)
    assert "findScenesBySceneFingerprints" in payload["query"]
    assert payload["variables"] == {
        "fingerprints": [
            [
                {"algorithm": "OSHASH", "hash": "0123456789abcdef"},
                {"algorithm": "PHASH", "hash": "fedcba9876543210"},
            ]
        ]
    }


async def test_scene_lookup_uses_exact_title_date_and_studio() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        fixture = (
            "stashdb-studios.json" if "queryStudios" in payload["query"] else "stashdb-scenes.json"
        )
        return httpx.Response(200, request=request, json=_fixture(fixture))

    adapter = StashdbAdapter.from_configuration(
        endpoint="https://stashdb.example/graphql",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    candidate = await adapter.find_by_site_date_title(
        site="Example Studio", release_date=date(2024, 2, 14), title="Known Scene"
    )

    assert candidate is not None
    assert candidate.provider_id == "scene-known-123"
    assert len(requests) == 2
    studio_query, scene_query = (json.loads(request.content) for request in requests)
    assert studio_query["variables"] == {"input": {"name": "Example Studio", "per_page": 25}}
    assert scene_query["variables"] == {
        "input": {
            "title": "Known Scene",
            "date": {"value": "2024-02-14", "modifier": "EQUALS"},
            "studios": {"value": ["studio-42"], "modifier": "INCLUDES"},
            "per_page": 25,
        }
    }


async def test_title_search_maps_recorded_scenes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=_fixture("stashdb-scenes.json"))

    adapter = StashdbAdapter.from_configuration(
        endpoint="https://stashdb.example/graphql",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    candidates = await adapter.search(title="Known Scene", performers=("Alice Example",))

    assert [candidate.provider_id for candidate in candidates] == ["scene-known-123"]


def test_missing_key_disables_the_adapter_without_a_request() -> None:
    assert (
        StashdbAdapter.from_configuration(endpoint="https://stashdb.example/graphql", api_key=None)
        is None
    )


async def test_rate_limit_is_exposed_to_the_metadata_cascade() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request, headers={"Retry-After": "12"})

    adapter = StashdbAdapter.from_configuration(
        endpoint="https://stashdb.example/graphql",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    with pytest.raises(MetadataRateLimitError, match="12") as error:
        await adapter.search(title="Known Scene", performers=())
    assert error.value.retry_after_seconds == 12
