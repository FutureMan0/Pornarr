"""SABnzbd adapter behaviour against recorded and protocol responses."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pornarr_integrations.downloaders import DownloadState
from pornarr_integrations.sabnzbd import (
    SABNZBD_HISTORY_STATE_MAP,
    SABNZBD_QUEUE_STATE_MAP,
    SabnzbdAdapter,
    map_sabnzbd_history_state,
    map_sabnzbd_queue_state,
    parse_history_slot,
    parse_queue,
)

FIXTURES = Path(__file__).parent / "fixtures"
API_KEY = "not-a-real-api-key"
NZO_ID = "2e3f13dc-a1e9-4bd4-9c2f-293cb1594fc4"


def response(request: httpx.Request, payload: object) -> httpx.Response:
    return httpx.Response(200, request=request, json=payload)


@pytest.mark.parametrize(
    ("sabnzbd_state", "expected"),
    [
        ("Paused", DownloadState.PAUSED),
        ("Checking", DownloadState.CHECKING),
        ("Downloading", DownloadState.DOWNLOADING),
        ("Fetching", DownloadState.DOWNLOADING),
        ("Grabbing", DownloadState.METADATA),
        ("Propagating", DownloadState.QUEUED),
        ("Queued", DownloadState.QUEUED),
        ("Deleted", DownloadState.FAILED),
    ],
)
def test_all_sabnzbd_queue_states_map_explicitly(
    sabnzbd_state: str, expected: DownloadState
) -> None:
    assert map_sabnzbd_queue_state(sabnzbd_state) is expected


@pytest.mark.parametrize(
    ("sabnzbd_state", "expected"),
    [
        ("Completed", DownloadState.COMPLETED),
        ("Failed", DownloadState.FAILED),
        ("Queued", DownloadState.IMPORTING),
        ("QuickCheck", DownloadState.REPAIRING),
        ("Verifying", DownloadState.REPAIRING),
        ("Repairing", DownloadState.REPAIRING),
        ("Fetching", DownloadState.DOWNLOADING),
        ("Extracting", DownloadState.IMPORTING),
        ("Moving", DownloadState.IMPORTING),
        ("Running", DownloadState.IMPORTING),
    ],
)
def test_all_sabnzbd_history_states_map_explicitly(
    sabnzbd_state: str, expected: DownloadState
) -> None:
    assert map_sabnzbd_history_state(sabnzbd_state) is expected


def test_sabnzbd_state_mappings_have_no_silent_fallback() -> None:
    assert len(SABNZBD_QUEUE_STATE_MAP) == 8
    assert len(SABNZBD_HISTORY_STATE_MAP) == 10
    with pytest.raises(ValueError, match="unsupported SABnzbd queue state"):
        map_sabnzbd_queue_state("new-state")
    with pytest.raises(ValueError, match="unsupported SABnzbd history state"):
        map_sabnzbd_history_state("new-state")


def test_recorded_sabnzbd_queue_response_is_parsed() -> None:
    recorded = json.loads((FIXTURES / "sabnzbd-queue.json").read_text())

    jobs = parse_queue(recorded["queue"])

    assert len(jobs) == 1
    assert jobs[0].client_job_id == NZO_ID
    assert jobs[0].state is DownloadState.PAUSED
    assert jobs[0].size_bytes == 1_138_816_450
    assert jobs[0].remaining_bytes == 1_138_816_450
    assert jobs[0].download_speed_bytes == 0
    assert jobs[0].eta_seconds is None


async def test_api_key_is_authenticated_before_a_connection_is_accepted() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, {"auth": "apikey"})

    adapter = SabnzbdAdapter(transport=httpx.MockTransport(handler))

    await adapter.test_connection(
        host="sabnzbd.example", port=8080, url_base="", credentials=API_KEY
    )

    assert requests[0].url.path == "/api"
    assert dict(requests[0].url.params) == {"output": "json", "mode": "auth", "key": API_KEY}


async def test_adds_url_and_nzb_file_with_category_priority_and_pause() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, {"status": True, "nzo_ids": [NZO_ID]})

    adapter = SabnzbdAdapter(transport=httpx.MockTransport(handler))
    connection = {"host": "sabnzbd.example", "port": 8080, "url_base": "", "credentials": API_KEY}

    assert (
        await adapter.add_url(
            **connection,
            url="https://indexer.example/release.nzb",
            category="pornarr",
            priority=1,
            paused=False,
        )
        == NZO_ID
    )
    assert (
        await adapter.add_nzb_file(
            **connection,
            nzb_file=b"<nzb></nzb>",
            filename="release.nzb",
            category="pornarr",
            priority=1,
            paused=True,
        )
        == NZO_ID
    )

    url_request, file_request = requests
    assert dict(url_request.url.params) == {
        "output": "json",
        "apikey": API_KEY,
        "mode": "addurl",
        "name": "https://indexer.example/release.nzb",
        "cat": "pornarr",
        "priority": "1",
        "pp": "2",
    }
    assert b'name="nzbfile"; filename="release.nzb"' in file_request.content
    assert b'name="priority"' in file_request.content
    assert b"-2" in file_request.content


async def test_reads_queue_and_history() -> None:
    recorded_queue = json.loads((FIXTURES / "sabnzbd-queue.json").read_text())
    history_slot = {
        "nzo_id": NZO_ID,
        "name": "release",
        "category": "pornarr",
        "status": "Extracting",
        "postproc_time": 19,
        "fail_message": "",
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params["mode"] == "queue":
            return response(request, recorded_queue)
        return response(request, {"history": {"slots": [history_slot]}})

    adapter = SabnzbdAdapter(transport=httpx.MockTransport(handler))
    connection = {"host": "sabnzbd.example", "port": 8080, "url_base": "", "credentials": API_KEY}

    queue = await adapter.list_queue(**connection)
    history = await adapter.list_history(**connection)

    assert queue[0].state is DownloadState.PAUSED
    assert history[0].state is DownloadState.IMPORTING
    assert [request.url.params["mode"] for request in requests] == ["queue", "history"]


async def test_controls_and_history_phases_keep_repair_and_import_distinct() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, {"status": True, "nzo_ids": [NZO_ID]})

    adapter = SabnzbdAdapter(transport=httpx.MockTransport(handler))
    connection = {"host": "sabnzbd.example", "port": 8080, "url_base": "", "credentials": API_KEY}

    await adapter.pause(**connection, client_job_id=NZO_ID)
    await adapter.resume(**connection, client_job_id=NZO_ID)
    await adapter.delete(**connection, client_job_id=NZO_ID, delete_files=False)
    await adapter.delete(**connection, client_job_id=NZO_ID, delete_files=True)

    repair = parse_history_slot(
        {
            "nzo_id": NZO_ID,
            "name": "release",
            "category": "pornarr",
            "status": "Repairing",
            "postproc_time": 12,
            "fail_message": "",
        }
    )
    extracting = parse_history_slot(
        {
            "nzo_id": NZO_ID,
            "name": "release",
            "category": "pornarr",
            "status": "Extracting",
            "postproc_time": 19,
            "fail_message": "",
        }
    )
    failed_repair = parse_history_slot(
        {
            "nzo_id": NZO_ID,
            "name": "release",
            "category": "pornarr",
            "status": "Failed",
            "postproc_time": 24,
            "fail_message": "PAR2 verification failed",
        }
    )

    assert repair.state is DownloadState.REPAIRING
    assert extracting.state is DownloadState.IMPORTING
    assert extracting.post_processing_seconds == 19
    assert failed_repair.state is DownloadState.FAILED
    assert failed_repair.error == "PAR2 verification failed"
    assert failed_repair.post_processing_seconds == 24
    assert [dict(request.url.params) for request in requests] == [
        {"output": "json", "apikey": API_KEY, "mode": "queue", "name": "pause", "value": NZO_ID},
        {"output": "json", "apikey": API_KEY, "mode": "queue", "name": "resume", "value": NZO_ID},
        {
            "output": "json",
            "apikey": API_KEY,
            "mode": "queue",
            "name": "delete",
            "value": NZO_ID,
            "del_files": "0",
        },
        {
            "output": "json",
            "apikey": API_KEY,
            "mode": "queue",
            "name": "delete",
            "value": NZO_ID,
            "del_files": "1",
        },
    ]
