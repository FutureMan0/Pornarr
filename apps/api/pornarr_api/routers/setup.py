"""One-time first-run setup."""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, hash_password
from pornarr_api.errors import ErrorResponse
from pornarr_api.routers.admin_download_clients import DownloadClientConnectionError
from pornarr_api.routers.admin_indexers import IndexerConnectionError, safe_reason
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.indexer import Indexer, IndexerStats
from pornarr_db.models.metadata_provider import MetadataProvider
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.user import User, UserRole
from pornarr_integrations.downloaders import DownloadClientAdapter
from pornarr_integrations.indexers import IndexerAdapter
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/setup", tags=["setup"])
Session = Annotated[AsyncSession, Depends(database_session)]
SETUP_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

# The adapters that exist, keyed the same way app.state.*_adapters is: an
# unknown implementation is a validation failure, not a row that quietly
# configures nothing.
IndexerImplementation = Literal["torznab", "newznab"]
DownloadClientImplementation = Literal["qbittorrent", "sabnzbd"]
MetadataProviderImplementation = Literal["stashdb", "tpdb"]

# Setup collects an implementation, not a protocol or a display name - both are
# implied by which adapter was chosen, so they are filled in here rather than
# asked of the operator a second time.
INDEXER_PROTOCOLS: dict[str, str] = {"torznab": "torrent", "newznab": "usenet"}
INDEXER_NAMES: dict[str, str] = {"torznab": "Torznab", "newznab": "Newznab"}
DOWNLOAD_CLIENT_PROTOCOLS: dict[str, str] = {"qbittorrent": "torrent", "sabnzbd": "usenet"}
DOWNLOAD_CLIENT_NAMES: dict[str, str] = {"qbittorrent": "qBittorrent", "sabnzbd": "SABnzbd"}


class SetupAlreadyCompletedError(PornarrError):
    code = "SETUP_ALREADY_COMPLETED"
    status = 409


class SetupPathInvalidError(PornarrError):
    code = "SETUP_PATH_INVALID"
    status = 422


class SetupPasswordTooWeakError(PornarrError):
    """The first administrator's password does not meet the rule the wizard states.

    The rule was enforced only by `passwordStrength` in the browser, so anything
    that skipped the wizard — curl, a script, a second tab posting the form —
    could create an administrator with a one-character password and sign in with
    it. `/setup/complete` is exempt from CSRF, because nobody holds a session
    during a first run, so this endpoint is reachable by anything that can see
    the port.

    Its own code rather than a bare 422: the frontend turns a code into a cause
    and a next step, and "the password is too short" is not something a generic
    validation failure can say.
    """

    code = "SETUP_PASSWORD_TOO_WEAK"
    status = 422


class SetupIndexerWrite(BaseModel):
    implementation: IndexerImplementation
    base_url: Annotated[str, Field(min_length=1, max_length=512)]
    api_key: SecretStr


class SetupDownloadClientWrite(BaseModel):
    implementation: DownloadClientImplementation
    host: Annotated[str, Field(min_length=1, max_length=512)]
    port: Annotated[int, Field(ge=1, le=65535)]
    credentials: SecretStr
    category: str | None = None


class SetupMetadataProviderWrite(BaseModel):
    implementation: MetadataProviderImplementation
    api_key: SecretStr
    endpoint: Annotated[str | None, Field(max_length=512)] = None


class SetupIndexerTestResponse(BaseModel):
    categories: list[dict[str, str]]


# The rule the account step states, and the one `passwordStrength` in
# `apps/web/src/routes/setup/setup-route.tsx` enforces: twelve characters from at
# least two of lower case, upper case, digits and symbols. Enforced here so that
# the server and the browser cannot disagree about what the product promised.
PASSWORD_MINIMUM_LENGTH = 12
PASSWORD_MINIMUM_CHARACTER_CLASSES = 2
_PASSWORD_CHARACTER_CLASSES = (
    re.compile(r"[a-z]"),
    re.compile(r"[A-Z]"),
    re.compile(r"\d"),
    re.compile(r"[^\w\s]"),
)


def _password_character_classes(password: str) -> int:
    return sum(1 for pattern in _PASSWORD_CHARACTER_CLASSES if pattern.search(password))


def _validate_password(password: str) -> None:
    classes = _password_character_classes(password)
    if len(password) >= PASSWORD_MINIMUM_LENGTH and classes >= PASSWORD_MINIMUM_CHARACTER_CLASSES:
        return
    # The length that was sent is not context — it is a fact about the password
    # and belongs nowhere near a log. What goes back is the rule, so a client
    # that is not the wizard can state it too.
    raise SetupPasswordTooWeakError(
        "The administrator password does not meet the minimum rule.",
        minimum_length=PASSWORD_MINIMUM_LENGTH,
        minimum_character_classes=PASSWORD_MINIMUM_CHARACTER_CLASSES,
    )


class SetupWrite(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: SecretStr
    library_path: Annotated[str, Field(min_length=1, max_length=1024)]
    indexer: SetupIndexerWrite | None = None
    download_client: SetupDownloadClientWrite | None = None
    metadata_provider: SetupMetadataProviderWrite | None = None


class SetupPathValidationWrite(BaseModel):
    library_path: Annotated[str, Field(min_length=1, max_length=1024)]


class SetupPathValidationResponse(BaseModel):
    same_filesystem_as_downloads: bool
    warning: str | None


class SetupCompleteResponse(BaseModel):
    username: str
    same_filesystem_as_downloads: bool
    warning: str | None


@router.get("/status")
async def setup_status(session: Session) -> dict[str, bool]:
    return {"configured": await session.scalar(select(User.id).limit(1)) is not None}


@router.post(
    "/validate-library-path", response_model=SetupPathValidationResponse, responses=SETUP_ERRORS
)
async def validate_library_path(
    payload: SetupPathValidationWrite, request: Request, session: Session
) -> SetupPathValidationResponse:
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise SetupAlreadyCompletedError("The instance has already been configured.")
    _, _, same_filesystem = _validate_library_path(
        payload.library_path, request.app.state.settings.data_path
    )
    warning = (
        None if same_filesystem else "different filesystem from downloads; imports cannot hardlink"
    )
    return SetupPathValidationResponse(
        same_filesystem_as_downloads=same_filesystem,
        warning=warning,
    )


@router.post("/test-indexer", response_model=SetupIndexerTestResponse, responses=SETUP_ERRORS)
async def test_setup_indexer(
    payload: SetupIndexerWrite, request: Request, session: Session
) -> SetupIndexerTestResponse:
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise SetupAlreadyCompletedError("The instance has already been configured.")
    categories = await _test_indexer_connection(
        request, payload.implementation, payload.base_url, payload.api_key.get_secret_value()
    )
    return SetupIndexerTestResponse(categories=categories)


@router.post("/test-download-client", status_code=204, responses=SETUP_ERRORS)
async def test_setup_download_client(
    payload: SetupDownloadClientWrite, request: Request, session: Session
) -> None:
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise SetupAlreadyCompletedError("The instance has already been configured.")
    await _test_download_client_connection(
        request,
        payload.implementation,
        payload.host,
        payload.port,
        payload.credentials.get_secret_value(),
    )


@router.post(
    "/complete", response_model=SetupCompleteResponse, status_code=201, responses=SETUP_ERRORS
)
async def complete_setup(
    payload: SetupWrite, request: Request, session: Session
) -> SetupCompleteResponse:
    profile = await session.scalar(
        select(ContentFilterProfile)
        .where(ContentFilterProfile.scope == FilterProfileScope.GLOBAL)
        .with_for_update()
    )
    if await session.scalar(select(User.id).limit(1)) is not None:
        raise SetupAlreadyCompletedError("The instance has already been configured.")
    # Before anything is tested or touched: a refusal here creates nothing and
    # tells an unauthenticated caller nothing about the server's filesystem.
    _validate_password(payload.password.get_secret_value())
    path, free_space_bytes, same_filesystem = _validate_library_path(
        payload.library_path, request.app.state.settings.data_path
    )
    # Re-tested here rather than trusted from the earlier /test-* call: the two
    # requests are independent, and nothing stops a client from calling
    # /complete directly with a configuration nobody ever tested.
    indexer_categories: list[dict[str, str]] = []
    if payload.indexer is not None:
        indexer_categories = await _test_indexer_connection(
            request,
            payload.indexer.implementation,
            payload.indexer.base_url,
            payload.indexer.api_key.get_secret_value(),
        )
    if payload.download_client is not None:
        await _test_download_client_connection(
            request,
            payload.download_client.implementation,
            payload.download_client.host,
            payload.download_client.port,
            payload.download_client.credentials.get_secret_value(),
        )
    if profile is None:
        profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
        session.add(profile)
        profile.rules = [
            ContentFilterRule(kind=kind, pattern="", action=FilterAction.REJECT, enabled=False)
            for kind in FilterRuleKind
        ]
    admin = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
        role=UserRole.ADMIN,
    )
    session.add(admin)
    await session.flush()
    session.add(AutomationRule(user_id=admin.id))
    session.add(RootFolder(path=str(path), enabled=True, free_space_bytes=free_space_bytes))
    if payload.indexer is not None:
        indexer = Indexer(
            name=INDEXER_NAMES[payload.indexer.implementation],
            protocol=INDEXER_PROTOCOLS[payload.indexer.implementation],
            implementation=payload.indexer.implementation,
            base_url=payload.indexer.base_url.rstrip("/"),
            api_key=payload.indexer.api_key.get_secret_value(),
            categories=indexer_categories,
            health="healthy",
            last_tested_at=datetime.now(UTC),
        )
        session.add(indexer)
        await session.flush()
        session.add(IndexerStats(indexer_id=indexer.id))
    if payload.download_client is not None:
        session.add(
            DownloadClient(
                name=DOWNLOAD_CLIENT_NAMES[payload.download_client.implementation],
                protocol=DOWNLOAD_CLIENT_PROTOCOLS[payload.download_client.implementation],
                implementation=payload.download_client.implementation,
                host=payload.download_client.host,
                port=payload.download_client.port,
                credentials=payload.download_client.credentials.get_secret_value(),
                category=payload.download_client.category,
                health="healthy",
                last_tested_at=datetime.now(UTC),
            )
        )
    if payload.metadata_provider is not None:
        session.add(
            MetadataProvider(
                implementation=payload.metadata_provider.implementation,
                endpoint=payload.metadata_provider.endpoint,
                api_key=payload.metadata_provider.api_key.get_secret_value(),
            )
        )
    warning = (
        None if same_filesystem else "different filesystem from downloads; imports cannot hardlink"
    )
    return SetupCompleteResponse(
        username=admin.username,
        same_filesystem_as_downloads=same_filesystem,
        warning=warning,
    )


async def _test_indexer_connection(
    request: Request, implementation: str, base_url: str, api_key: str
) -> list[dict[str, str]]:
    adapters: dict[str, IndexerAdapter] = getattr(request.app.state, "indexer_adapters", {})
    adapter = adapters.get(implementation)
    if adapter is None:
        raise IndexerConnectionError(
            f"No adapter is registered for {implementation!r}.", reason="adapter is not installed"
        )
    try:
        categories = await adapter.test_connection(base_url=base_url.rstrip("/"), api_key=api_key)
    except IndexerConnectionError:
        raise
    except Exception as error:
        raise IndexerConnectionError(
            "The indexer connection failed.", reason=safe_reason(error, api_key)
        ) from error
    return [{"id": category.id, "name": category.name} for category in categories]


async def _test_download_client_connection(
    request: Request, implementation: str, host: str, port: int, credentials: str
) -> None:
    adapters: dict[str, DownloadClientAdapter] = getattr(
        request.app.state, "download_client_adapters", {}
    )
    adapter = adapters.get(implementation)
    if adapter is None:
        raise DownloadClientConnectionError(f"No adapter is registered for {implementation!r}.")
    try:
        await adapter.test_connection(host=host, port=port, url_base="", credentials=credentials)
    except DownloadClientConnectionError:
        raise
    except Exception as error:
        raise DownloadClientConnectionError("The download client connection failed.") from error


def _validate_library_path(value: str, data_path: Path) -> tuple[Path, int, bool]:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SetupPathInvalidError(
            "The library path is unavailable.", reason="unavailable"
        ) from exc
    if not path.is_dir():
        raise SetupPathInvalidError("The library path is not a directory.", reason="not_directory")
    if not os.access(path, os.R_OK | os.W_OK):
        raise SetupPathInvalidError(
            "The library path must be readable and writable.", reason="not_accessible"
        )
    try:
        return path, shutil.disk_usage(path).free, path.stat().st_dev == data_path.stat().st_dev
    except OSError as exc:
        raise SetupPathInvalidError(
            "The library path is unavailable.", reason="unavailable"
        ) from exc
