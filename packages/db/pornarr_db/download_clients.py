"""Download-client selection rules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.download_client import DownloadClient
from pornarr_shared.errors import PornarrError


class NoHealthyDownloadClientError(PornarrError):
    code = "DOWNLOAD_CLIENT_UNAVAILABLE"
    status = 409


async def route_download_client(session: AsyncSession, protocol: str) -> DownloadClient:
    """Return the highest-priority enabled healthy client for a protocol."""
    client = await session.scalar(
        select(DownloadClient)
        .where(
            DownloadClient.protocol == protocol,
            DownloadClient.enabled.is_(True),
            DownloadClient.health == "healthy",
        )
        .order_by(DownloadClient.priority, DownloadClient.name)
    )
    if client is None:
        raise NoHealthyDownloadClientError(f"No healthy {protocol} download client is configured.")
    return client
