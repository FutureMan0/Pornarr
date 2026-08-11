"""Recorded Torznab and Newznab response coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from pornarr_integrations.health import CircuitBreaker, IndexerFailure, IndexerHealth
from pornarr_integrations.newznab import NewznabAdapter
from pornarr_integrations.newznab import parse_results as parse_newznab_results
from pornarr_integrations.torznab import TorznabResponseError, parse_results

FIXTURES = Path(__file__).parent / "indexers" / "fixtures"


def recording(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize(
    ("name", "count", "size", "categories", "info_hash"),
    [
        (
            "torznab-anime-tosho.xml",
            2,
            316477946,
            ("5070", "100001"),
            "2d69a861bef5a9f2cdf791b7328e37b7953205e1",
        ),
        (
            "torznab-hdaccess.xml",
            1,
            2538463390,
            ("5000", "5040"),
            "63e07ff523710ca268567dad344ce1e0e6b7e8a3",
        ),
    ],
)
def test_recorded_torznab_responses_preserve_torrent_fields(
    name: str, count: int, size: int, categories: tuple[str, ...], info_hash: str
) -> None:
    releases = parse_results(recording(name))

    assert len(releases) == count
    assert releases[0].size == size
    assert releases[0].categories == categories
    assert releases[0].info_hash == info_hash


@pytest.mark.parametrize(
    ("name", "group", "poster", "password_protected"),
    [
        ("newznab-nzbsu.xml", (), None, None),
        (
            "newznab-drunkenslug.xml",
            ("alt.binaries.classic.tv.shows",),
            "grizz <grizz@not.home>",
            False,
        ),
    ],
)
async def test_recorded_newznab_responses_preserve_usenet_fields(
    name: str, group: tuple[str, ...], poster: str | None, password_protected: bool | None
) -> None:
    class RecordedAdapter(NewznabAdapter):
        async def _request(self, base_url: str, api_key: str, params: dict[str, str]) -> str:
            return recording(name)

    releases = await RecordedAdapter().search(
        base_url="https://indexer.example/api", api_key="fixture-key", query="fixture"
    )

    assert parse_newznab_results is parse_results
    assert releases[0].groups == group
    assert releases[0].poster == poster
    assert releases[0].password_protected is password_protected
    assert releases[0].download_url is not None and "apikey=fixture-key" in releases[0].download_url


def test_empty_partial_and_malformed_recordings_are_distinct_cases() -> None:
    assert parse_results(recording("empty.xml")) == []
    with pytest.raises(TorznabResponseError):
        parse_results(recording("partial.xml"))
    with pytest.raises(TorznabResponseError) as malformed:
        parse_results(recording("malformed.xml"))

    assert malformed.value.failure is IndexerFailure.MALFORMED_RESPONSE


async def test_malformed_recording_opens_the_circuit_breaker() -> None:
    class Redis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def delete(self, *keys: str) -> int:
            for key in keys:
                self.values.pop(key, None)
            return len(keys)

        async def incr(self, key: str) -> int:
            self.values[key] = str(int(self.values.get(key, "0")) + 1)
            return int(self.values[key])

        async def expire(self, key: str, seconds: int) -> bool:
            return key in self.values and seconds > 0

    with pytest.raises(TorznabResponseError) as error:
        parse_results(recording("malformed.xml"))

    breaker = CircuitBreaker(Redis())
    health = IndexerHealth.HEALTHY
    for _ in range(3):
        outcome = await breaker.record_failure("malformed", health, error.value.failure)
        health = outcome.health

    assert health is IndexerHealth.UNHEALTHY
