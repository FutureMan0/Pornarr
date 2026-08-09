"""Quality definition and profile invariants."""

from __future__ import annotations

import pytest

from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (9_000_000, True),
        (10_000_000, False),
        (20_000_000, False),
        (21_000_000, True),
    ],
)
def test_quality_definition_flags_sizes_outside_its_bounds(size_bytes: int, expected: bool) -> None:
    definition = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=10,
        maximum_size_mb_per_minute=20,
    )

    assert definition.is_size_mislabelled(size_bytes=size_bytes, duration_seconds=60) is expected


def test_unknown_duration_is_mislabelled() -> None:
    definition = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=10,
        maximum_size_mb_per_minute=20,
    )

    assert definition.is_size_mislabelled(size_bytes=15_000_000, duration_seconds=0)


def test_profile_items_preserve_an_explicit_order() -> None:
    profile = QualityProfile(name="Default", is_default=True)
    lower = QualityDefinition(
        name="WEB 720p",
        resolution="720p",
        source="web",
        weight=10,
        minimum_size_mb_per_minute=5,
        maximum_size_mb_per_minute=20,
    )
    higher = QualityDefinition(
        name="WEB 1080p",
        resolution="1080p",
        source="web",
        weight=20,
        minimum_size_mb_per_minute=10,
        maximum_size_mb_per_minute=30,
    )

    profile.items = [
        QualityProfileItem(quality_definition=lower, position=0),
        QualityProfileItem(quality_definition=higher, position=1),
    ]

    assert [(item.quality_definition.name, item.position) for item in profile.items] == [
        ("WEB 720p", 0),
        ("WEB 1080p", 1),
    ]
