"""Administrator-only download-client configuration and connection tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.models.download_client import DownloadClient
from pornarr_db.models.user import User, UserRole
from pornarr_integrations.downloaders import DownloadClientAdapter
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/download-clients", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]


class DownloadClientConnectionError(PornarrError):
    code = "DOWNLOAD_CLIENT_CONNECTION_FAILED"
    status = 422


class DownloadClientWrite(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    protocol: Annotated[str, Field(min_length=1, max_length=32)]
    implementation: Annotated[str, Field(min_length=1, max_length=64)]
    host: Annotated[str, Field(min_length=1, max_length=512)]
    port: Annotated[int, Field(ge=1, le=65535)]
    url_base: str = ""
    credentials: SecretStr
    category: str | None = None
    priority: int = 0
    remove_completed: bool = False
    enabled: bool = True


class DownloadClientResponse(BaseModel):
    id: UUID
    name: str
    protocol: str
    implementation: str
    host: str
    port: int
    url_base: str
    category: str | None
    priority: int
    remove_completed: bool
    enabled: bool
    health: str
    last_error: str | None
    last_tested_at: datetime | None


class DownloadClientUpdate(DownloadClientWrite):
    pass


def response(client: DownloadClient) -> DownloadClientResponse:
    return DownloadClientResponse.model_validate(client, from_attributes=True)


async def client_or_404(session: AsyncSession, client_id: UUID) -> DownloadClient:
    client = await session.get(DownloadClient, client_id)
    if client is None:
        raise HTTPException(status_code=404)
    return client


def adapter_for(request: Request, client: DownloadClient) -> DownloadClientAdapter:
    adapters: dict[str, DownloadClientAdapter] = getattr(
        request.app.state, "download_client_adapters", {}
    )
    adapter = adapters.get(client.implementation)
    if adapter is None:
        raise DownloadClientConnectionError(
            f"No adapter is registered for {client.implementation!r}."
        )
    return adapter


@router.get("", response_model=list[DownloadClientResponse])
async def list_download_clients(_: Admin, session: Session) -> list[DownloadClientResponse]:
    clients = await session.scalars(
        select(DownloadClient).order_by(DownloadClient.priority, DownloadClient.name)
    )
    return [response(client) for client in clients]


@router.post("", response_model=DownloadClientResponse, status_code=201)
async def create_download_client(
    payload: DownloadClientWrite, _: Admin, session: Session
) -> DownloadClientResponse:
    client = DownloadClient(
        **payload.model_dump(exclude={"credentials"}),
        credentials=payload.credentials.get_secret_value(),
    )
    session.add(client)
    await session.flush()
    return response(client)


@router.post("/{client_id}/test", response_model=DownloadClientResponse)
async def test_download_client(
    client_id: UUID, request: Request, _: Admin, session: Session
) -> DownloadClientResponse:
    client = await client_or_404(session, client_id)
    try:
        await adapter_for(request, client).test_connection(
            host=client.host,
            port=client.port,
            url_base=client.url_base,
            credentials=client.credentials,
        )
    except DownloadClientConnectionError:
        raise
    except Exception as error:
        client.health = "unhealthy"
        client.last_error = str(error).replace(client.credentials, "[redacted]")
        client.last_tested_at = datetime.now(UTC)
        # `database_session` rolls back on any exception it sees, and this
        # handler is about to raise one - so the diagnosis has to be committed
        # here or the operator's failed test leaves no trace on the row.
        await session.commit()
        raise DownloadClientConnectionError("The download client connection failed.") from error
    client.health = "healthy"
    client.last_error = None
    client.last_tested_at = datetime.now(UTC)
    return response(client)


@router.put("/{client_id}", response_model=DownloadClientResponse)
async def update_download_client(
    client_id: UUID, payload: DownloadClientUpdate, _: Admin, session: Session
) -> DownloadClientResponse:
    client = await client_or_404(session, client_id)
    for field, value in payload.model_dump(exclude={"credentials"}).items():
        setattr(client, field, value)
    client.credentials = payload.credentials.get_secret_value()
    await session.flush()
    return response(client)


@router.delete("/{client_id}", status_code=204)
async def delete_download_client(client_id: UUID, _: Admin, session: Session) -> None:
    await session.delete(await client_or_404(session, client_id))
