"""SABnzbd API adapter with explicit post-processing phases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import httpx

from pornarr_integrations.downloaders import DownloadClientJob, DownloadState

SABNZBD_QUEUE_STATE_MAP: Final[dict[str, DownloadState]] = {
    "Paused": DownloadState.PAUSED,
    "Checking": DownloadState.CHECKING,
    "Downloading": DownloadState.DOWNLOADING,
    "Fetching": DownloadState.DOWNLOADING,
    "Grabbing": DownloadState.METADATA,
    "Propagating": DownloadState.QUEUED,
    "Queued": DownloadState.QUEUED,
    "Deleted": DownloadState.FAILED,
}

SABNZBD_HISTORY_STATE_MAP: Final[dict[str, DownloadState]] = {
    "Completed": DownloadState.COMPLETED,
    "Failed": DownloadState.FAILED,
    "Queued": DownloadState.IMPORTING,
    "QuickCheck": DownloadState.REPAIRING,
    "Verifying": DownloadState.REPAIRING,
    "Repairing": DownloadState.REPAIRING,
    "Fetching": DownloadState.DOWNLOADING,
    "Extracting": DownloadState.IMPORTING,
    "Moving": DownloadState.IMPORTING,
    "Running": DownloadState.IMPORTING,
}


class SabnzbdAuthenticationError(ValueError):
    """The configured SABnzbd API key is invalid."""


class SabnzbdProtocolError(ValueError):
    """SABnzbd returned a response the adapter cannot safely interpret."""


@dataclass(frozen=True)
class SabnzbdJob:
    client_job_id: str
    name: str
    category: str
    state: DownloadState
    size_bytes: int | None
    remaining_bytes: int | None
    download_speed_bytes: int | None
    eta_seconds: int | None
    error: str | None
    post_processing_seconds: int | None
    output_path: str | None


def map_sabnzbd_queue_state(value: str) -> DownloadState:
    """Map every queue job state SABnzbd documents; reject future values."""
    try:
        return SABNZBD_QUEUE_STATE_MAP[value]
    except KeyError as error:
        raise SabnzbdProtocolError(f"unsupported SABnzbd queue state {value!r}") from error


def map_sabnzbd_history_state(value: str) -> DownloadState:
    """Map history and post-processing states without collapsing their phases."""
    try:
        return SABNZBD_HISTORY_STATE_MAP[value]
    except KeyError as error:
        raise SabnzbdProtocolError(f"unsupported SABnzbd history state {value!r}") from error


def parse_queue(payload: Mapping[str, object]) -> list[SabnzbdJob]:
    """Convert a SABnzbd queue response into active download jobs."""
    slots = payload.get("slots")
    if not isinstance(slots, list) or not all(isinstance(slot, dict) for slot in slots):
        raise SabnzbdProtocolError("SABnzbd returned an invalid queue.")
    speed = _kibibytes_to_bytes(payload, "kbpersec")
    return [
        SabnzbdJob(
            client_job_id=_string(slot, "nzo_id"),
            name=_string(slot, "filename"),
            category=_string(slot, "cat"),
            state=map_sabnzbd_queue_state(_string(slot, "status")),
            size_bytes=_mebibytes_to_bytes(slot, "mb"),
            remaining_bytes=_mebibytes_to_bytes(slot, "mbleft"),
            download_speed_bytes=speed,
            eta_seconds=_duration(_string(slot, "timeleft")),
            error=None,
            post_processing_seconds=None,
            output_path=None,
        )
        for slot in slots
    ]


def parse_history_slot(payload: Mapping[str, object]) -> SabnzbdJob:
    """Convert a post-processing or terminal history slot into a download job."""
    failure = _string(payload, "fail_message")
    return SabnzbdJob(
        client_job_id=_string(payload, "nzo_id"),
        name=_string(payload, "name"),
        category=_string(payload, "category"),
        state=map_sabnzbd_history_state(_string(payload, "status")),
        size_bytes=None,
        remaining_bytes=None,
        download_speed_bytes=None,
        eta_seconds=None,
        error=failure or None,
        post_processing_seconds=_integer(payload, "postproc_time"),
        output_path=_optional_string(payload, "storage"),
    )


class SabnzbdAdapter:
    """Hide SABnzbd's API key and queue/history wire formats from callers."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        api_key = _api_key(credentials)
        payload = await self._call(
            host, port, url_base, api_key, {"mode": "auth", "key": api_key}, include_key=False
        )
        if payload.get("auth") != "apikey":
            raise SabnzbdAuthenticationError("SABnzbd rejected the configured API key.")

    async def add_url(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        url: str,
        category: str | None,
        priority: int,
        paused: bool,
    ) -> str:
        return await self._add(
            host,
            port,
            url_base,
            credentials,
            {"mode": "addurl", "name": url, **_add_options(category, priority, paused)},
        )

    async def add_nzb_file(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        nzb_file: bytes,
        filename: str,
        category: str | None,
        priority: int,
        paused: bool,
    ) -> str:
        return await self._add(
            host,
            port,
            url_base,
            credentials,
            {"mode": "addfile", **_add_options(category, priority, paused)},
            files={"nzbfile": (filename, nzb_file, "application/x-nzb")},
        )

    async def list_queue(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> list[SabnzbdJob]:
        payload = await self._call(host, port, url_base, _api_key(credentials), {"mode": "queue"})
        queue = payload.get("queue")
        if not isinstance(queue, dict):
            raise SabnzbdProtocolError("SABnzbd returned an invalid queue response.")
        return parse_queue(queue)

    async def list_history(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> list[SabnzbdJob]:
        payload = await self._call(host, port, url_base, _api_key(credentials), {"mode": "history"})
        history = payload.get("history")
        if not isinstance(history, dict):
            raise SabnzbdProtocolError("SABnzbd returned an invalid history response.")
        slots = history.get("slots")
        if not isinstance(slots, list) or not all(isinstance(slot, dict) for slot in slots):
            raise SabnzbdProtocolError("SABnzbd returned an invalid history.")
        return [parse_history_slot(slot) for slot in slots]

    async def list_jobs(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
    ) -> list[DownloadClientJob]:
        """Read SABnzbd's active queue and post-processing history as one batch."""
        queue = await self.list_queue(
            host=host, port=port, url_base=url_base, credentials=credentials
        )
        history = await self.list_history(
            host=host, port=port, url_base=url_base, credentials=credentials
        )
        return [
            DownloadClientJob(
                client_job_id=job.client_job_id,
                state=job.state,
                size_bytes=job.size_bytes,
                remaining_bytes=job.remaining_bytes,
                download_speed_bytes=job.download_speed_bytes,
                estimated_seconds=job.eta_seconds,
                error=job.error,
                output_path=job.output_path,
            )
            for job in [*queue, *history]
        ]

    async def pause(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> None:
        await self._control(host, port, url_base, credentials, "pause", client_job_id)

    async def resume(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> None:
        await self._control(host, port, url_base, credentials, "resume", client_job_id)

    async def set_priority(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        client_job_id: str,
        priority: int,
    ) -> None:
        await self._control(
            host,
            port,
            url_base,
            credentials,
            "priority",
            client_job_id,
            value2=_queue_priority(priority),
        )

    async def delete(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        client_job_id: str,
        delete_files: bool,
    ) -> None:
        await self._control(
            host,
            port,
            url_base,
            credentials,
            "delete",
            client_job_id,
            del_files="1" if delete_files else "0",
        )

    async def cancel(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> None:
        """Remove a cancelled request's incomplete SABnzbd job and files."""
        await self.delete(
            host=host,
            port=port,
            url_base=url_base,
            credentials=credentials,
            client_job_id=client_job_id,
            delete_files=True,
        )

    async def _add(
        self,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        options: dict[str, str],
        *,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> str:
        payload = await self._call(
            host,
            port,
            url_base,
            _api_key(credentials),
            options,
            method="POST" if files else "GET",
            files=files,
        )
        nzo_ids = payload.get("nzo_ids")
        if payload.get("status") is not True or not isinstance(nzo_ids, list) or len(nzo_ids) != 1:
            raise SabnzbdProtocolError("SABnzbd did not return an added job ID.")
        nzo_id = nzo_ids[0]
        if not isinstance(nzo_id, str):
            raise SabnzbdProtocolError("SABnzbd returned an invalid job ID.")
        return nzo_id

    async def _control(
        self,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        name: str,
        client_job_id: str,
        **options: str,
    ) -> None:
        payload = await self._call(
            host,
            port,
            url_base,
            _api_key(credentials),
            {"mode": "queue", "name": name, "value": client_job_id, **options},
        )
        if payload.get("status") is not True:
            raise SabnzbdProtocolError(f"SABnzbd did not {name} the job.")

    async def _call(
        self,
        host: str,
        port: int,
        url_base: str,
        api_key: str,
        options: dict[str, str],
        *,
        include_key: bool = True,
        method: str = "GET",
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Mapping[str, object]:
        query = {"output": "json"}
        if include_key:
            query["apikey"] = api_key
        query.update(options)
        async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
            response = await client.request(
                method,
                _api_url(host, port, url_base),
                params=None if files else query,
                data=query if files else None,
                files=files,
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise SabnzbdProtocolError("SABnzbd rejected the request.")
        return payload


def _add_options(category: str | None, priority: int, paused: bool) -> dict[str, str]:
    return {
        "cat": category or "*",
        "priority": "-2" if paused else _queue_priority(priority),
        "pp": "2",
    }


def _queue_priority(priority: int) -> str:
    """Map Pornarr's five request levels onto SABnzbd's four native levels."""
    if priority >= 100:
        return "2"
    if priority >= 80:
        return "1"
    if priority >= 60:
        return "0"
    return "-1"


def _api_url(host: str, port: int, url_base: str) -> str:
    path = url_base.strip("/")
    return f"http://{host}:{port}" + (f"/{path}" if path else "") + "/api"


def _api_key(credentials: str) -> str:
    api_key = credentials.strip()
    if not api_key:
        raise SabnzbdAuthenticationError("A SABnzbd API key is required.")
    return api_key


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise SabnzbdProtocolError(f"SABnzbd field {key!r} must be a string.")
    return value


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SabnzbdProtocolError(f"SABnzbd field {key!r} must be a string.")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SabnzbdProtocolError(f"SABnzbd field {key!r} must be an integer.")
    return value


def _mebibytes_to_bytes(payload: Mapping[str, object], key: str) -> int:
    return int(_decimal(payload, key) * 1024 * 1024)


def _kibibytes_to_bytes(payload: Mapping[str, object], key: str) -> int:
    return int(_decimal(payload, key) * 1024)


def _decimal(payload: Mapping[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise SabnzbdProtocolError(f"SABnzbd field {key!r} must be a number.")
    try:
        return float(value)
    except ValueError as error:
        raise SabnzbdProtocolError(f"SABnzbd field {key!r} must be a number.") from error


def _duration(value: str) -> int | None:
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise SabnzbdProtocolError("SABnzbd returned an invalid time left.") from error
    duration = hours * 3600 + minutes * 60 + seconds
    return duration or None
