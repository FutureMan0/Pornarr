"""Protocol-neutral submission of an already validated external release."""

from __future__ import annotations

from typing import cast

from pornarr_integrations.downloaders import TorrentSubmissionAdapter, UsenetSubmissionAdapter


class UnsupportedReleaseError(ValueError):
    """The configured client cannot safely accept this release."""


class ReleaseSubmissionError(RuntimeError):
    """The download client rejected an otherwise valid submission."""


def release_protocol(*, magnet_url: str | None, download_url: str | None) -> str:
    if magnet_url is not None:
        return "torrent"
    if download_url is not None:
        return "usenet"
    raise UnsupportedReleaseError("The selected release has no supported download URL.")


async def submit_release(
    adapter: object,
    *,
    protocol: str,
    host: str,
    port: int,
    url_base: str,
    credentials: str,
    category: str | None,
    priority: int,
    magnet_url: str | None,
    info_hash: str | None,
    download_url: str | None,
) -> str:
    """Send a cached release to a client and return its client-side identifier."""

    try:
        if protocol == "torrent":
            if info_hash is None or magnet_url is None or not hasattr(adapter, "add_magnet"):
                raise UnsupportedReleaseError("The selected torrent cannot be submitted safely.")
            await cast(TorrentSubmissionAdapter, adapter).add_magnet(
                host=host,
                port=port,
                url_base=url_base,
                credentials=credentials,
                magnet=magnet_url,
                category=category,
                paused=False,
            )
            return info_hash
        if download_url is None or not hasattr(adapter, "add_url"):
            raise UnsupportedReleaseError(
                "The selected release cannot be submitted to this client."
            )
        return await cast(UsenetSubmissionAdapter, adapter).add_url(
            host=host,
            port=port,
            url_base=url_base,
            credentials=credentials,
            url=download_url,
            category=category,
            priority=priority,
            paused=False,
        )
    except UnsupportedReleaseError:
        raise
    except Exception as error:
        raise ReleaseSubmissionError("The download client rejected the release.") from error
