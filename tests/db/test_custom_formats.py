"""Custom-format evaluation and transfer contracts."""

from __future__ import annotations

from pornarr_db.custom_formats import (
    custom_format_score,
    export_custom_format,
    import_custom_format,
    matches_custom_format,
)
from pornarr_db.models.custom_formats import (
    CustomFormat,
    CustomFormatCondition,
    CustomFormatField,
    CustomFormatOperator,
)


def test_required_condition_does_not_match_when_its_field_is_absent() -> None:
    format_ = CustomFormat(
        name="WEB source",
        score=10,
        conditions=[
            CustomFormatCondition(
                field=CustomFormatField.SOURCE,
                operator=CustomFormatOperator.EQUALS,
                value="web",
                required=True,
            )
        ],
    )

    assert not matches_custom_format(format_, {"title": "Example"})


def test_negated_condition_matches_a_different_present_value() -> None:
    format_ = CustomFormat(
        name="Not Xvid",
        score=15,
        conditions=[
            CustomFormatCondition(
                field=CustomFormatField.CODEC,
                operator=CustomFormatOperator.EQUALS,
                value="xvid",
                negate=True,
                required=True,
            )
        ],
    )

    assert matches_custom_format(format_, {"codec": "h264"})
    assert not matches_custom_format(format_, {"codec": "xvid"})


def test_optional_conditions_require_at_least_one_match() -> None:
    format_ = CustomFormat(
        name="Preferred source",
        score=20,
        conditions=[
            CustomFormatCondition(
                field=CustomFormatField.SOURCE,
                operator=CustomFormatOperator.EQUALS,
                value="web",
                required=False,
            ),
            CustomFormatCondition(
                field=CustomFormatField.PROTOCOL,
                operator=CustomFormatOperator.EQUALS,
                value="usenet",
                required=False,
            ),
        ],
    )

    assert matches_custom_format(format_, {"source": "web", "protocol": "torrent"})
    assert not matches_custom_format(format_, {"source": "bluray", "protocol": "torrent"})


def test_scores_accumulate_across_matching_formats() -> None:
    formats = [
        CustomFormat(
            name="WEB source",
            score=10,
            conditions=[
                CustomFormatCondition(
                    field=CustomFormatField.SOURCE,
                    operator=CustomFormatOperator.EQUALS,
                    value="web",
                )
            ],
        ),
        CustomFormat(
            name="HEVC codec",
            score=25,
            conditions=[
                CustomFormatCondition(
                    field=CustomFormatField.CODEC,
                    operator=CustomFormatOperator.EQUALS,
                    value="hevc",
                )
            ],
        ),
    ]

    assert custom_format_score(formats, {"source": "web", "codec": "hevc"}) == 35


def test_exported_format_imports_unchanged() -> None:
    original = CustomFormat(
        name="Preferred release",
        score=15,
        conditions=[
            CustomFormatCondition(
                field=CustomFormatField.FLAGS,
                operator=CustomFormatOperator.CONTAINS,
                value="proper",
                negate=True,
                required=False,
            )
        ],
    )

    restored = import_custom_format(export_custom_format(original))

    assert export_custom_format(restored) == export_custom_format(original)
