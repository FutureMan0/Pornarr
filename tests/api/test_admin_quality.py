"""Administrator quality-profile and custom-format API behaviour."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.quality import QualityDefinition
from pornarr_db.models.user import UserRole
from tests.api.test_auth import create_user, csrf_headers, login

pytest_plugins = ("tests.api.test_auth",)


async def test_admin_manages_ordered_quality_profiles_and_custom_formats(app, client) -> None:
    qualities = await _qualities(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    created = await client.post(
        "/api/admin/quality/profiles",
        json={
            "name": "Cinema",
            "quality_definition_ids": [str(qualities[0].id), str(qualities[2].id)],
            "cutoff_quality_id": str(qualities[2].id),
            "minimum_custom_format_score": 23,
            "is_default": True,
        },
        headers=csrf_headers(client),
    )

    assert created.status_code == 201
    profile = created.json()
    assert [quality["name"] for quality in profile["qualities"]] == ["WEB 720p", "WEB 2160p"]
    assert profile["cutoff_quality_id"] == str(qualities[2].id)

    updated = await client.put(
        f"/api/admin/quality/profiles/{profile['id']}",
        json={
            "name": "Cinema",
            "quality_definition_ids": [str(qualities[2].id), str(qualities[0].id)],
            "cutoff_quality_id": str(qualities[2].id),
            "minimum_custom_format_score": 7,
            "is_default": True,
        },
        headers=csrf_headers(client),
    )
    formats = await client.post(
        "/api/admin/quality/custom-formats",
        json={
            "name": "Prefer HEVC",
            "score": 7,
            "conditions": [
                {
                    "field": "codec",
                    "operator": "equals",
                    "value": "hevc",
                    "required": True,
                    "negate": False,
                }
            ],
        },
        headers=csrf_headers(client),
    )

    assert updated.status_code == 200
    assert [quality["name"] for quality in updated.json()["qualities"]] == [
        "WEB 2160p",
        "WEB 720p",
    ]
    assert formats.status_code == 201
    assert formats.json()["conditions"][0]["field"] == "codec"
    format_id = formats.json()["id"]
    revised = await client.put(
        f"/api/admin/quality/custom-formats/{format_id}",
        json={
            "name": "Prefer HEVC",
            "score": 9,
            "conditions": [
                {
                    "field": "source",
                    "operator": "contains",
                    "value": "web",
                    "required": True,
                    "negate": False,
                }
            ],
        },
        headers=csrf_headers(client),
    )
    deleted = await client.delete(
        f"/api/admin/quality/custom-formats/{format_id}", headers=csrf_headers(client)
    )

    assert revised.status_code == 200
    assert revised.json()["score"] == 9
    assert deleted.status_code == 204
    assert (await client.get("/api/admin/quality/custom-formats")).json() == []


async def test_quality_preview_scores_unsaved_format_edits_and_explains_the_verdict(
    app, client
) -> None:
    qualities = await _qualities(app)
    admin = await create_user(app, role=UserRole.ADMIN)
    await login(client, admin.username, "correct horse battery staple")

    preview = await client.post(
        "/api/admin/quality/preview",
        json={
            "release_name": "Example.Scene.2025.1080p.WEB-DL.x265.PROPER",
            "profile": {
                "quality_definition_ids": [str(qualities[1].id), str(qualities[2].id)],
                "cutoff_quality_id": str(qualities[2].id),
                "minimum_custom_format_score": 25,
            },
            "custom_formats": [
                {
                    "name": "Prefer HEVC",
                    "score": 7,
                    "conditions": [
                        {
                            "field": "codec",
                            "operator": "equals",
                            "value": "hevc",
                            "required": True,
                            "negate": False,
                        }
                    ],
                }
            ],
        },
        headers=csrf_headers(client),
    )

    assert preview.status_code == 200
    result = preview.json()
    assert result["quality"]["name"] == "WEB 1080p"
    assert result["score"] == 27
    assert result["verdict"] == "grab"
    assert result["reason"] == "no_existing_file"
    assert result["matched_custom_formats"] == [{"name": "Prefer HEVC", "score": 7}]
    assert result["fields"] == {
        "resolution": "1080p",
        "source": "web-dl",
        "codec": "hevc",
        "flags": ["proper"],
    }


async def test_quality_administration_is_restricted_to_administrators(app, client) -> None:
    user = await create_user(app)
    await login(client, user.username, "correct horse battery staple")

    assert (await client.get("/api/admin/quality/profiles")).status_code == 403


async def _qualities(app) -> list[QualityDefinition]:
    async with AsyncSession(app.state.engine, expire_on_commit=False) as session:
        values = [
            QualityDefinition(
                name="WEB 720p",
                resolution="720p",
                source="web",
                weight=10,
                minimum_size_mb_per_minute=5,
                maximum_size_mb_per_minute=20,
            ),
            QualityDefinition(
                name="WEB 1080p",
                resolution="1080p",
                source="web",
                weight=20,
                minimum_size_mb_per_minute=10,
                maximum_size_mb_per_minute=35,
            ),
            QualityDefinition(
                name="WEB 2160p",
                resolution="2160p",
                source="web",
                weight=30,
                minimum_size_mb_per_minute=15,
                maximum_size_mb_per_minute=80,
            ),
        ]
        session.add_all(values)
        await session.commit()
        return values
