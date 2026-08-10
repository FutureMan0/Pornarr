"""qBittorrent WebUI adapter with cookie re-authentication."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

import httpx

from pornarr_integrations.downloaders import DownloadState

QBITTORRENT_STATE_MAP: Final[dict[str, DownloadState]] = {
    "error": DownloadState.FAILED,
    "missingFiles": DownloadState.FAILED,
    "uploading": DownloadState.SEEDING,
    "pausedUP": DownloadState.PAUSED,
    "queuedUP": DownloadState.COMPLETED,
    "stalledUP": DownloadState.SEEDING,
    "checkingUP": DownloadState.CHECKING,
    "forcedUP": DownloadState.SEEDING,
    "allocating": DownloadState.QUEUED,
    "downloading": DownloadState.DOWNLOADING,
    "metaDL": DownloadState.METADATA,
    "pausedDL": DownloadState.PAUSED,
    "queuedDL": DownloadState.QUEUED,
    "stalledDL": DownloadState.STALLED,
    "checkingDL": DownloadState.CHECKING,
    "forcedDL": DownloadState.DOWNLOADING,
    "checkingResumeData": DownloadState.CHECKING,
    "moving": DownloadState.MOVING,
    "unknown": DownloadState.UNKNOWN,
}


class QbittorrentAuthenticationError(ValueError):
    """The configured qBittorrent credentials cannot establish a session."""


class QbittorrentProtocolError(ValueError):
    """qBittorrent returned a response the adapter cannot safely interpret."""


@dataclass(frozen=True)
class QbittorrentCredentials:
    username: str
    password: str

    @classmethod
    def parse(cls, value: str) -> QbittorrentCredentials:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as error:
            raise QbittorrentAuthenticationError(
                "qBittorrent credentials must be a JSON object with username and password."
            ) from error
        if not isinstance(payload, dict):
            raise QbittorrentAuthenticationError(
                "qBittorrent credentials must be a JSON object with username and password."
            )
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise QbittorrentAuthenticationError(
                "qBittorrent credentials must be a JSON object with username and password."
            )
        return cls(username=username, password=password)


@dataclass(frozen=True)
class QbittorrentTorrent:
    client_job_id: str
    category: str
    state: DownloadState
    name: str
    progress: float
    size_bytes: int
    remaining_bytes: int
    download_speed_bytes: int
    eta_seconds: int | None
    seeders: int
    save_path: str
    ratio: float
    max_ratio: float
    seeding_time_seconds: int
    max_seeding_time_seconds: int

    @property
    def has_met_seeding_policy(self) -> bool:
        if self.progress < 1 or self.state not in {
            DownloadState.COMPLETED,
            DownloadState.PAUSED,
            DownloadState.SEEDING,
        }:
            return False
        ratio_reached = self.max_ratio >= 0 and self.ratio >= self.max_ratio
        time_reached = (
            self.max_seeding_time_seconds >= 0
            and self.seeding_time_seconds >= self.max_seeding_time_seconds
        )
        return ratio_reached or time_reached


def map_qbittorrent_state(value: str) -> DownloadState:
    """Map every documented qBittorrent state; reject unknown future values."""
    try:
        return QBITTORRENT_STATE_MAP[value]
    except KeyError as error:
        raise QbittorrentProtocolError(f"unsupported qBittorrent state {value!r}") from error


def parse_torrent(payload: Mapping[str, object]) -> QbittorrentTorrent:
    """Convert one `/torrents/info` item into the internal download shape."""
    eta = _integer(payload, "eta")
    return QbittorrentTorrent(
        client_job_id=_string(payload, "hash"),
        category=_string(payload, "category"),
        state=map_qbittorrent_state(_string(payload, "state")),
        name=_string(payload, "name"),
        progress=_float(payload, "progress"),
        size_bytes=_integer(payload, "size"),
        remaining_bytes=_integer(payload, "amount_left"),
        download_speed_bytes=_integer(payload, "dlspeed"),
        eta_seconds=None if eta < 0 or eta >= 8_640_000 else eta,
        seeders=_integer(payload, "num_seeds"),
        save_path=_string(payload, "save_path"),
        ratio=_float(payload, "ratio"),
        max_ratio=_float(payload, "max_ratio"),
        seeding_time_seconds=_integer(payload, "seeding_time"),
        max_seeding_time_seconds=_integer(payload, "max_seeding_time"),
    )


class QbittorrentAdapter:
    """Hide qBittorrent's login cookie and WebUI wire protocol from callers."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def test_connection(
        self, *, host: str, port: int, url_base: str, credentials: str
    ) -> None:
        async with self._authenticated_client(host, port, url_base, credentials) as (client, auth):
            await self._request(client, auth, "GET", "app/version")

    async def add_magnet(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        magnet: str,
        category: str | None,
        paused: bool,
    ) -> None:
        data = {"urls": magnet, "paused": str(paused).lower()}
        if category is not None:
            data["category"] = category
        await self._add(host, port, url_base, credentials, data=data)

    async def add_torrent_file(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        torrent_file: bytes,
        filename: str,
        category: str | None,
        paused: bool,
    ) -> None:
        data = {"paused": str(paused).lower()}
        if category is not None:
            data["category"] = category
        await self._add(
            host,
            port,
            url_base,
            credentials,
            data=data,
            files={"torrents": (filename, torrent_file, "application/x-bittorrent")},
        )

    async def list_torrents(
        self,
        *,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        category: str | None = None,
        client_job_ids: list[str] | None = None,
    ) -> list[QbittorrentTorrent]:
        params: dict[str, str] = {}
        if category is not None:
            params["category"] = category
        if client_job_ids:
            params["hashes"] = "|".join(client_job_ids)
        async with self._authenticated_client(host, port, url_base, credentials) as (client, auth):
            response = await self._request(client, auth, "GET", "torrents/info", params=params)
        payload = response.json()
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise QbittorrentProtocolError("qBittorrent returned an invalid torrent list.")
        return [parse_torrent(item) for item in payload]

    async def pause(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> None:
        await self._control(host, port, url_base, credentials, "torrents/stop", client_job_id)

    async def resume(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> None:
        await self._control(host, port, url_base, credentials, "torrents/start", client_job_id)

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
            "torrents/delete",
            client_job_id,
            deleteFiles=str(delete_files).lower(),
        )

    async def delete_after_seeding(
        self, *, host: str, port: int, url_base: str, credentials: str, client_job_id: str
    ) -> bool:
        torrents = await self.list_torrents(
            host=host,
            port=port,
            url_base=url_base,
            credentials=credentials,
            client_job_ids=[client_job_id],
        )
        if len(torrents) != 1 or not torrents[0].has_met_seeding_policy:
            return False
        await self.delete(
            host=host,
            port=port,
            url_base=url_base,
            credentials=credentials,
            client_job_id=client_job_id,
            delete_files=False,
        )
        return True

    async def _add(
        self,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        *,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> None:
        async with self._authenticated_client(host, port, url_base, credentials) as (client, auth):
            response = await self._request(
                client, auth, "POST", "torrents/add", data=data, files=files
            )
        if response.text.strip() == "Fails.":
            raise QbittorrentProtocolError("qBittorrent rejected the torrent.")

    async def _control(
        self,
        host: str,
        port: int,
        url_base: str,
        credentials: str,
        path: str,
        client_job_id: str,
        **data: str,
    ) -> None:
        async with self._authenticated_client(host, port, url_base, credentials) as (client, auth):
            await self._request(client, auth, "POST", path, data={"hashes": client_job_id, **data})

    @asynccontextmanager
    async def _authenticated_client(
        self, host: str, port: int, url_base: str, credentials: str
    ) -> AsyncIterator[tuple[httpx.AsyncClient, QbittorrentCredentials]]:
        auth = QbittorrentCredentials.parse(credentials)
        base_url, referer = _urls(host, port, url_base)
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Referer": referer},
            follow_redirects=True,
            timeout=10,
            transport=self._transport,
        ) as client:
            await self._login(client, auth)
            yield client, auth

    async def _request(
        self,
        client: httpx.AsyncClient,
        auth: QbittorrentCredentials,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        response = await client.request(method, path, params=params, data=data, files=files)
        if response.status_code in {401, 403}:
            await self._login(client, auth)
            response = await client.request(method, path, params=params, data=data, files=files)
        response.raise_for_status()
        return response

    async def _login(self, client: httpx.AsyncClient, auth: QbittorrentCredentials) -> None:
        response = await client.post(
            "auth/login", data={"username": auth.username, "password": auth.password}
        )
        if response.status_code not in {200, 204} or response.text.strip() == "Fails.":
            raise QbittorrentAuthenticationError("qBittorrent rejected the configured credentials.")


def _urls(host: str, port: int, url_base: str) -> tuple[str, str]:
    path = url_base.strip("/")
    referer = f"http://{host}:{port}" + (f"/{path}" if path else "/")
    return f"{referer.rstrip('/')}/api/v2/", referer


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise QbittorrentProtocolError(f"qBittorrent field {key!r} must be a string.")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise QbittorrentProtocolError(f"qBittorrent field {key!r} must be an integer.")
    return value


def _float(payload: Mapping[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise QbittorrentProtocolError(f"qBittorrent field {key!r} must be a number.")
    return float(value)
