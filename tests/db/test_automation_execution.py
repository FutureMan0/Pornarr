"""Fail-closed automatic request execution."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.automation import AutomationCandidate, execute_automation
from pornarr_db.base import Base
from pornarr_db.models.audit import AuditLog
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.download import DownloadJob
from pornarr_db.models.request import Request
from pornarr_db.models.root_folders import RootFolder
from pornarr_db.models.storage import DailyStorageUsage
from pornarr_db.models.user import User
from pornarr_db.settings import RuntimeSettings
from pornarr_db.storage import utc_day
from pornarr_shared.config import Settings


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as database_session:
            yield database_session
    finally:
        await engine.dispose()


def _settings(**overrides: object) -> RuntimeSettings:
    defaults = Settings(
        app_secret="0123456789abcdef0123456789abcdef",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/15",
    )
    return RuntimeSettings.from_defaults(
        defaults, {"default_auto_downloads_enabled": True, **overrides}
    )


async def _enabled_rule(session: AsyncSession) -> User:
    user = User(id=uuid4(), username="automation-owner", password_hash="hash")
    session.add_all(
        (
            user,
            AutomationRule(
                user_id=user.id,
                enabled=True,
                minimum_score=60,
                daily_download_limit_gb=1,
                max_concurrent_jobs=1,
                max_downloads_per_day=1,
                allowed_qualities=["1080p"],
                blocked_tags=["blocked"],
                blocked_performers=["blocked performer"],
            ),
            RootFolder(
                path="/library", free_space_bytes=900_000_000, total_space_bytes=1_000_000_000
            ),
        )
    )
    await session.flush()
    return user


def _candidate(user_id: UUID) -> AutomationCandidate:
    return AutomationCandidate(
        user_id=user_id,
        release_guid="release-1",
        query="Example title",
        score=0.8,
        size_bytes=100_000_000,
        quality="1080p",
        metadata_confident=True,
        tags=(),
        performers=(),
        breakdown={"recommendation_score": 0.8},
    )


async def test_automation_creates_a_low_priority_request_and_audits_the_decision(
    session: AsyncSession,
) -> None:
    user = await _enabled_rule(session)

    decision = await execute_automation(session, _candidate(user.id), _settings())

    assert decision.created is True
    request = await session.scalar(select(Request))
    usage = await session.get(DailyStorageUsage, (user.id, decision.day))
    audit = await session.scalar(select(AuditLog))
    assert request is not None
    assert request.priority == 40
    assert request.selected_release_guid == "release-1"
    assert usage is not None
    assert usage.downloaded_bytes == 0
    assert usage.download_count == 0
    assert usage.reserved_bytes == 100_000_000
    assert usage.reserved_download_count == 1
    assert audit is not None
    assert audit.action == "automation.requested"
    assert audit.context["score"] == 80.0


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        ("disable", "disabled"),
        ("low_score", "score_threshold"),
        ("blocked_tag", "blocked_tag"),
        ("blocked_performer", "blocked_performer"),
        ("low_confidence", "metadata_confidence"),
        ("wrong_quality", "quality"),
        ("duplicate", "duplicate"),
        ("daily_budget", "daily_budget"),
        ("concurrency", "concurrency"),
        ("disk", "free_disk"),
        ("global_kill_switch", "global_kill_switch"),
    ],
)
async def test_automation_refuses_every_unsafe_precondition(
    session: AsyncSession, prepare: str, reason: str
) -> None:
    user = await _enabled_rule(session)
    candidate = _candidate(user.id)
    settings = _settings()
    if prepare == "disable":
        rule = await session.get(AutomationRule, user.id)
        assert rule is not None
        rule.enabled = False
    elif prepare == "low_score":
        candidate = replace(candidate, score=0.59)
    elif prepare == "blocked_tag":
        candidate = replace(candidate, tags=("blocked",))
    elif prepare == "blocked_performer":
        candidate = replace(candidate, performers=("blocked performer",))
    elif prepare == "low_confidence":
        candidate = replace(candidate, metadata_confident=False)
    elif prepare == "wrong_quality":
        candidate = replace(candidate, quality="2160p")
    elif prepare == "duplicate":
        session.add(
            DownloadJob(
                client_name="client",
                protocol="torrent",
                release_guid=candidate.release_guid,
                status="queued",
            )
        )
    elif prepare == "daily_budget":
        session.add(
            DailyStorageUsage(
                user_id=user.id,
                day=utc_day(),
                downloaded_bytes=950_000_000,
                download_count=1,
                reserved_bytes=0,
                reserved_download_count=0,
            )
        )
    elif prepare == "concurrency":
        session.add(
            Request(
                user_id=user.id,
                query="already active",
                status="queued",
                priority=40,
            )
        )
    elif prepare == "disk":
        folder = await session.scalar(select(RootFolder))
        assert folder is not None
        folder.free_space_bytes = 200_000_000
    elif prepare == "global_kill_switch":
        settings = _settings(default_auto_downloads_enabled=False)
    await session.flush()

    decision = await execute_automation(session, candidate, settings)

    assert decision.created is False
    assert decision.reason == reason
    assert await session.scalar(select(Request).where(Request.query == candidate.query)) is None
    audit = await session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()))
    assert audit is not None
    assert audit.action == "automation.refused"
    assert audit.context["reason"] == reason
