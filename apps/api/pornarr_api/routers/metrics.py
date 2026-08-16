"""Prometheus exposition, explicitly disabled until an administrator enables it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session
from pornarr_db.settings import get_runtime_settings
from pornarr_shared.metrics import render_metrics

router = APIRouter(tags=["metrics"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, session: Session) -> Response:
    settings = await get_runtime_settings(session, request.app.state.settings)
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404)
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
