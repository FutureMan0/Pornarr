"""ThePornDB REST adapter behaviour against recorded protocol responses."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from pornarr_integrations.metadata import MetadataRateLimitError
from pornarr_integrations.metadata.tpdb import TpdbAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


async def test_site_date_title_lookup_maps_a_tpdb_only_scene() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=_fixture("tpdb-scenes.json"))

    adapter = TpdbAdapter.from_configuration(
        endpoint="https://api.theporndb.example",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    candidate = await adapter.find_by_site_date_title(
        site="Example Studio", release_date=date(2024, 2, 14), title="TPDB-only Scene"
    )

    assert candidate is not None
    assert candidate.provider_id == "tpdb-scene-123"
    assert candidate.site == candidate.studio == "Example Studio"
    assert candidate.performers == ("Alice Example", "Bob Example")
    assert candidate.tags == ("Outdoor", "HD")
    assert candidate.raw["site_id"] == "tpdb-site-42"
    assert candidate.raw["performer_ids"] == ["tpdb-performer-7", "tpdb-performer-8"]
    assert candidate.raw["tag_ids"] == ["tpdb-tag-2", "tpdb-tag-3"]

    assert requests[0].url.path == "/scenes"
    assert requests[0].headers["Authorization"] == "Bearer fixture-key"
    assert dict(requests[0].url.params) == {
        "site": "Example Studio",
        "date": "2024-02-14",
        "date_operation": "=",
        "title": "TPDB-only Scene",
        "per_page": "25",
    }


async def test_title_search_maps_recorded_scenes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=_fixture("tpdb-scenes.json"))

    adapter = TpdbAdapter.from_configuration(
        endpoint="https://api.theporndb.example",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    candidates = await adapter.search(title="TPDB-only Scene", performers=("Alice Example",))

    assert [candidate.provider_id for candidate in candidates] == ["tpdb-scene-123"]


def test_missing_key_disables_the_adapter_without_a_request() -> None:
    assert (
        TpdbAdapter.from_configuration(endpoint="https://api.theporndb.example", api_key=None)
        is None
    )


async def test_rate_limit_is_exposed_to_the_metadata_cascade() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request, headers={"Retry-After": "12"})

    adapter = TpdbAdapter.from_configuration(
        endpoint="https://api.theporndb.example",
        api_key="fixture-key",
        transport=httpx.MockTransport(handler),
    )

    assert adapter is not None
    with pytest.raises(MetadataRateLimitError, match="12") as error:
        await adapter.search(title="TPDB-only Scene", performers=())
    assert error.value.retry_after_seconds == 12
