"""The operator's content filter, which ADR 0017 says is the only one there is."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.audit import AuditLog
from pornarr_db.models.filters import (
    ContentFilterProfile,
    ContentFilterRule,
    FilterAction,
    FilterProfileScope,
    FilterRuleKind,
)
from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)

_ALL_KINDS = [kind.value for kind in FilterRuleKind]


async def seed_global_profile(app) -> None:
    """What `alembic/versions/0004_add_content_filter_profiles.py` inserts."""
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        profile = ContentFilterProfile(scope=FilterProfileScope.GLOBAL)
        profile.rules = [
            ContentFilterRule(kind=kind, pattern="", action=FilterAction.REJECT, enabled=False)
            for kind in FilterRuleKind
        ]
        session.add(profile)
        await session.commit()


def _rules(**overrides: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "kind": kind,
            "pattern": "",
            "action": "reject",
            "enabled": False,
            **overrides.get(kind, {}),
        }
        for kind in _ALL_KINDS
    ]


async def test_the_shipped_profile_is_readable_and_every_rule_is_off(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    response = await client.get("/api/admin/filters/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "global"
    assert [rule["kind"] for rule in body["rules"]] == _ALL_KINDS
    assert all(rule["enabled"] is False and rule["pattern"] == "" for rule in body["rules"])


async def test_an_administrator_turns_rules_on_and_the_change_is_audited(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    updated = await client.put(
        "/api/admin/filters/profile",
        json={
            "rules": _rules(
                term={"pattern": " Teen ", "action": "reject", "enabled": True},
                minimum_confidence={"pattern": "0.6", "action": "quarantine", "enabled": True},
                unknown_file_type={"action": "quarantine", "enabled": True},
            )
        },
        headers=csrf_headers(client),
    )

    assert updated.status_code == 200, updated.json()
    by_kind = {rule["kind"]: rule for rule in updated.json()["rules"]}
    assert by_kind["term"] == {**by_kind["term"], "pattern": "Teen", "action": "reject"}
    assert by_kind["term"]["enabled"] is True
    assert by_kind["minimum_confidence"]["action"] == "quarantine"
    assert by_kind["tag"]["enabled"] is False

    # Read back through a second request: the point of the endpoint is that the
    # decision survives the response that reported it.
    stored = (await client.get("/api/admin/filters/profile")).json()
    assert {rule["kind"] for rule in stored["rules"] if rule["enabled"]} == {
        "term",
        "minimum_confidence",
        "unknown_file_type",
    }
    async with AsyncSession(app.state.engine) as session:
        entries = list(await session.scalars(select(AuditLog)))
    assert [entry.action for entry in entries] == ["filter_profile.updated"]
    assert entries[0].actor_id == admin.id
    assert entries[0].context["enabled_kinds"] == [
        "minimum_confidence",
        "term",
        "unknown_file_type",
    ]


async def test_an_enabled_term_rule_must_say_what_it_matches(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    refused = await client.put(
        "/api/admin/filters/profile",
        json={"rules": _rules(tag={"enabled": True})},
        headers=csrf_headers(client),
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "FILTER_CONFIGURATION_INVALID"
    assert refused.json()["context"] == {"reason": "pattern_required", "kind": "tag"}
    stored = (await client.get("/api/admin/filters/profile")).json()
    assert all(rule["enabled"] is False for rule in stored["rules"])


async def test_a_confidence_threshold_outside_zero_to_one_is_refused(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    refused = await client.put(
        "/api/admin/filters/profile",
        json={"rules": _rules(minimum_confidence={"pattern": "7", "enabled": True})},
        headers=csrf_headers(client),
    )

    assert refused.status_code == 422
    assert refused.json()["context"] == {
        "reason": "confidence_invalid",
        "kind": "minimum_confidence",
    }


async def test_a_rule_that_reads_no_pattern_refuses_one(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    refused = await client.put(
        "/api/admin/filters/profile",
        json={"rules": _rules(unknown_performer_age={"pattern": "18", "enabled": True})},
        headers=csrf_headers(client),
    )

    assert refused.status_code == 422
    assert refused.json()["context"] == {
        "reason": "pattern_not_allowed",
        "kind": "unknown_performer_age",
    }


async def test_a_plain_user_can_neither_read_nor_change_the_profile(app, client) -> None:
    await seed_global_profile(app)
    member = await create_user(app, username="member", role=UserRole.USER)
    await login(client, member.username, "correct horse battery staple")

    read = await client.get("/api/admin/filters/profile")
    written = await client.put(
        "/api/admin/filters/profile",
        json={"rules": _rules(term={"pattern": "anything", "enabled": True})},
        headers=csrf_headers(client),
    )

    assert read.status_code == 403
    assert read.json()["code"] == "FORBIDDEN"
    assert written.status_code == 403


async def test_a_write_without_the_csrf_header_is_refused(app, client) -> None:
    await seed_global_profile(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    written = await client.put(
        "/api/admin/filters/profile",
        json={"rules": _rules(term={"pattern": "anything", "enabled": True})},
    )

    assert written.status_code == 403
    assert written.json()["code"] == "CSRF_FAILED"
