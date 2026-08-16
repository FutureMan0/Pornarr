"""Automation rule defaults and administrator caps."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from pornarr_db.automation import (
    AutomationLimitError,
    AutomationRuleWrite,
    save_automation_rule,
)
from pornarr_db.base import Base
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.user import User
from pornarr_db.settings import RuntimeSettings
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


def _limits() -> RuntimeSettings:
    defaults = Settings(
        app_secret="0123456789abcdef0123456789abcdef",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/15",
    )
    return RuntimeSettings.from_defaults(defaults, {})


async def test_rule_defaults_are_disabled_and_match_the_plan(session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    session.add(user)
    await session.flush()

    rule = AutomationRule(user_id=user.id)
    session.add(rule)
    await session.flush()

    assert rule.enabled is False
    assert rule.daily_download_limit_gb == 10
    assert rule.max_concurrent_jobs == 2
    assert rule.max_downloads_per_day == 3


async def test_user_cannot_save_a_rule_above_administrator_caps(session: AsyncSession) -> None:
    user = User(id=uuid4(), username="alice", password_hash="hash")
    session.add(user)
    await session.flush()

    with pytest.raises(AutomationLimitError, match="daily_download_limit_gb"):
        await save_automation_rule(
            session,
            user.id,
            AutomationRuleWrite(daily_download_limit_gb=11),
            _limits(),
        )


async def test_user_can_save_a_rule_within_administrator_caps(session: AsyncSession) -> None:
    user = User(id=uuid4(), username="alice", password_hash="hash")
    session.add(user)
    await session.flush()

    rule = await save_automation_rule(
        session,
        user.id,
        AutomationRuleWrite(
            enabled=True,
            minimum_score=70,
            daily_download_limit_gb=9,
            max_concurrent_jobs=1,
            max_downloads_per_day=2,
            allowed_qualities=["1080p"],
            blocked_tags=["excluded"],
            blocked_performers=["excluded performer"],
        ),
        _limits(),
    )

    assert rule.enabled is True
    assert rule.minimum_score == 70
    assert rule.allowed_qualities == ["1080p"]
