"""qBittorrent adapter behaviour against recorded and protocol responses."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from pornarr_integrations.qbittorrent import (
    QBITTORRENT_STATE_MAP,
    DownloadState,
    QbittorrentAdapter,
    map_qbittorrent_state,
    parse_torrent,
)

FIXTURES = Path(__file__).parent / "fixtures"
CREDENTIALS = json.dumps({"username": "admin", "password": "not-a-real-password"})
TORRENT_HASH = "0123456789abcdef0123456789abcdef01234567"


def response(request: httpx.Request, status_code: int = 200, text: str = "") -> httpx.Response:
    return httpx.Response(status_code, request=request, text=text)


def json_response(request: httpx.Request, payload: object) -> httpx.Response:
    return httpx.Response(200, request=request, json=payload)


@pytest.mark.parametrize(
    ("qbittorrent_state", "expected"),
    [
        ("error", DownloadState.FAILED),
        ("missingFiles", DownloadState.FAILED),
        ("uploading", DownloadState.SEEDING),
        ("pausedUP", DownloadState.PAUSED),
        ("stoppedUP", DownloadState.PAUSED),
        ("queuedUP", DownloadState.COMPLETED),
        ("stalledUP", DownloadState.SEEDING),
        ("checkingUP", DownloadState.CHECKING),
        ("forcedUP", DownloadState.SEEDING),
        ("allocating", DownloadState.QUEUED),
        ("downloading", DownloadState.DOWNLOADING),
        ("metaDL", DownloadState.METADATA),
        ("forcedMetaDL", DownloadState.METADATA),
        ("pausedDL", DownloadState.PAUSED),
        ("stoppedDL", DownloadState.PAUSED),
        ("queuedDL", DownloadState.QUEUED),
        ("stalledDL", DownloadState.STALLED),
        ("checkingDL", DownloadState.CHECKING),
        ("forcedDL", DownloadState.DOWNLOADING),
        ("checkingResumeData", DownloadState.CHECKING),
        ("moving", DownloadState.MOVING),
        ("unknown", DownloadState.UNKNOWN),
    ],
)
def test_all_documented_qbittorrent_states_map_explicitly(
    qbittorrent_state: str, expected: DownloadState
) -> None:
    assert map_qbittorrent_state(qbittorrent_state) is expected


def test_qbittorrent_state_mapping_has_no_silent_fallback() -> None:
    assert len(QBITTORRENT_STATE_MAP) == 22
    with pytest.raises(ValueError, match="unsupported qBittorrent state"):
        map_qbittorrent_state("new-state")


def test_recorded_torrent_response_is_parsed() -> None:
    recorded = json.loads((FIXTURES / "qbittorrent-torrents-info.json").read_text())

    torrent = parse_torrent(recorded[0])

    assert torrent.client_job_id == TORRENT_HASH
    assert torrent.category == "pornarr"
    assert torrent.state is DownloadState.QUEUED
    assert torrent.progress == 0
    assert torrent.remaining_bytes == 0
    assert torrent.eta_seconds is None
    assert torrent.save_path == "/downloads"
    assert torrent.output_path == "/downloads/pornarr-fixture"


async def test_session_expiry_reauthenticates_before_returning_a_failure() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["referer"] == "http://qbittorrent.example:8080/"
        if request.url.path == "/api/v2/auth/login":
            return response(request, 204)
        if request.url.path == "/api/v2/app/version" and len(requests) == 2:
            return response(request, 403)
        return response(request, text="v5.2.3")

    adapter = QbittorrentAdapter(transport=httpx.MockTransport(handler))

    await adapter.test_connection(
        host="qbittorrent.example", port=8080, url_base="", credentials=CREDENTIALS
    )

    assert [request.url.path for request in requests] == [
        "/api/v2/auth/login",
        "/api/v2/app/version",
        "/api/v2/auth/login",
        "/api/v2/app/version",
    ]


async def test_reads_all_jobs_in_one_queue_request() -> None:
    recorded = json.loads((FIXTURES / "qbittorrent-torrents-info.json").read_text())
    recorded[0]["content_path"] = "/downloads/exact-content-path"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/login"):
            return response(request, 204)
        return json_response(request, recorded)

    adapter = QbittorrentAdapter(transport=httpx.MockTransport(handler))

    jobs = await adapter.list_jobs(
        host="qbittorrent.example",
        port=8080,
        url_base="",
        credentials=CREDENTIALS,
    )

    assert [job.client_job_id for job in jobs] == [TORRENT_HASH]
    assert jobs[0].estimated_seconds is None
    assert jobs[0].output_path == "/downloads/exact-content-path"
    assert [request.url.path for request in requests] == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
    ]


async def test_adds_magnet_and_torrent_file_with_category_and_paused_start() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(
            request, 204 if request.url.path.endswith("/auth/login") else 200, text="Ok."
        )

    adapter = QbittorrentAdapter(transport=httpx.MockTransport(handler))
    connection = {
        "host": "qbittorrent.example",
        "port": 8080,
        "url_base": "",
        "credentials": CREDENTIALS,
    }

    await adapter.add_magnet(
        **connection,
        magnet="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        category="pornarr",
        paused=True,
    )
    await adapter.add_torrent_file(
        **connection,
        torrent_file=b"d4:infod4:name4:testee",
        filename="release.torrent",
        category="pornarr",
        paused=True,
    )

    add_requests = [request for request in requests if request.url.path.endswith("/torrents/add")]
    magnet_form = parse_qs(add_requests[0].content.decode())
    assert magnet_form["urls"] == ["magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"]
    assert magnet_form["category"] == ["pornarr"]
    assert magnet_form["paused"] == ["true"]
    assert b'name="torrents"; filename="release.torrent"' in add_requests[1].content
    assert b'name="category"' in add_requests[1].content
    assert b'name="paused"' in add_requests[1].content


async def test_a_torrent_the_client_already_has_is_not_a_failure() -> None:
    """qBittorrent answers 409 for a duplicate, and that is the wanted outcome."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return response(request, 204, text="Ok.")
        return response(request, 409, text="Torrent is already present.")

    adapter = QbittorrentAdapter(transport=httpx.MockTransport(handler))

    await adapter.add_magnet(
        host="qbittorrent.example",
        port=8080,
        url_base="",
        credentials=CREDENTIALS,
        magnet="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        category="pornarr",
        paused=False,
    )


async def test_controls_and_seeding_policy_protected_removal() -> None:
    recorded = json.loads((FIXTURES / "qbittorrent-torrents-info.json").read_text())[0]
    recorded.update(state="uploading", progress=1, max_ratio=1, ratio=0.5)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/login"):
            return response(request, 204)
        if request.url.path.endswith("/torrents/info"):
            return json_response(request, [recorded])
        return response(request)

    adapter = QbittorrentAdapter(transport=httpx.MockTransport(handler))
    connection = {
        "host": "qbittorrent.example",
        "port": 8080,
        "url_base": "",
        "credentials": CREDENTIALS,
    }

    await adapter.pause(**connection, client_job_id=TORRENT_HASH)
    await adapter.resume(**connection, client_job_id=TORRENT_HASH)
    await adapter.delete(**connection, client_job_id=TORRENT_HASH, delete_files=False)
    await adapter.delete(**connection, client_job_id=TORRENT_HASH, delete_files=True)
    await adapter.cancel(**connection, client_job_id=TORRENT_HASH)
    assert not await adapter.delete_after_seeding(**connection, client_job_id=TORRENT_HASH)

    recorded["ratio"] = 1
    assert await adapter.delete_after_seeding(**connection, client_job_id=TORRENT_HASH)

    post_forms = [
        parse_qs(request.content.decode()) for request in requests if request.method == "POST"
    ]
    assert {"hashes": [TORRENT_HASH]} in post_forms
    assert {"hashes": [TORRENT_HASH], "deleteFiles": ["false"]} in post_forms
    assert {"hashes": [TORRENT_HASH], "deleteFiles": ["true"]} in post_forms
    assert post_forms.count({"hashes": [TORRENT_HASH], "deleteFiles": ["true"]}) == 2
