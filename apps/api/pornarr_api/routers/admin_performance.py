"""Administrator visibility into measurements backing download estimates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, require_role
from pornarr_db.models.statistics import PerformanceMetric
from pornarr_db.models.user import User, UserRole
from pornarr_db.statistics import rolling_average

router = APIRouter(prefix="/admin/performance", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

_PERFORMANCE_DIMENSIONS: tuple[tuple[PerformanceMetric, str | None], ...] = (
    (PerformanceMetric.DOWNLOAD_SPEED, "usenet"),
    (PerformanceMetric.DOWNLOAD_SPEED, "torrent"),
    (PerformanceMetric.POST_PROCESSING_SECONDS_PER_GIB, "usenet"),
    (PerformanceMetric.IMPORT_SECONDS_PER_GIB, "hardlink"),
    (PerformanceMetric.IMPORT_SECONDS_PER_GIB, "copy"),
    (PerformanceMetric.DISK_WRITE_SPEED, None),
)


class PerformanceInputResponse(BaseModel):
    metric: PerformanceMetric
    scope: str | None
    value: float | None
    sample_count: int


@router.get("", response_model=list[PerformanceInputResponse])
async def list_performance_inputs(_: Admin, session: Session) -> list[PerformanceInputResponse]:
    """Show both measured and unknown inputs so estimate confidence is explainable."""

    result = []
    for metric, scope in _PERFORMANCE_DIMENSIONS:
        summary = await rolling_average(session, metric=metric, scope=scope)
        result.append(
            PerformanceInputResponse(
                metric=metric,
                scope=scope,
                value=summary.value,
                sample_count=summary.sample_count,
            )
        )
    return result
