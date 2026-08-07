"""Pure custom-format matching and JSON transfer helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeGuard

from pornarr_db.models.custom_formats import (
    CustomFormat,
    CustomFormatCondition,
    CustomFormatField,
    CustomFormatOperator,
)

_MISSING = object()


def matches_custom_format(format_: CustomFormat, fields: Mapping[str, object]) -> bool:
    """Match required conditions all together and optional conditions any-or-none."""
    required = [condition for condition in format_.conditions if condition.required]
    optional = [condition for condition in format_.conditions if not condition.required]
    return all(_matches(condition, fields) for condition in required) and (
        not optional or any(_matches(condition, fields) for condition in optional)
    )


def custom_format_score(formats: Iterable[CustomFormat], fields: Mapping[str, object]) -> int:
    """Sum scores from every custom format that matches the release properties."""
    return sum(format_.score for format_ in formats if matches_custom_format(format_, fields))


def export_custom_format(format_: CustomFormat) -> dict[str, object]:
    """Return a portable JSON-compatible representation."""
    return {
        "name": format_.name,
        "score": format_.score,
        "conditions": [
            {
                "field": condition.field.value,
                "operator": condition.operator.value,
                "value": condition.value,
                "negate": condition.negate,
                "required": condition.required,
            }
            for condition in format_.conditions
        ],
    }


def import_custom_format(document: Mapping[str, object]) -> CustomFormat:
    """Build an unsaved custom format from its portable representation."""
    conditions = document.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("Custom format conditions must be a list.")

    name = document.get("name")
    score = document.get("score")
    if not isinstance(name, str) or not _is_number(score):
        raise ValueError("Custom format needs a string name and numeric score.")

    format_ = CustomFormat(name=name, score=int(score))
    format_.conditions = [_import_condition(condition) for condition in conditions]
    return format_


def _import_condition(document: object) -> CustomFormatCondition:
    if not isinstance(document, Mapping):
        raise ValueError("Custom format condition must be an object.")
    return CustomFormatCondition(
        field=CustomFormatField(str(document["field"])),
        operator=CustomFormatOperator(str(document["operator"])),
        value=document["value"],
        negate=bool(document.get("negate", False)),
        required=bool(document.get("required", True)),
    )


def _matches(condition: CustomFormatCondition, fields: Mapping[str, object]) -> bool:
    candidate = fields.get(condition.field.value, _MISSING)
    if candidate is _MISSING:
        return False

    matched = _compare(candidate, condition.operator, condition.value)
    return not matched if condition.negate else matched


def _compare(candidate: object, operator: CustomFormatOperator, expected: object) -> bool:
    if operator is CustomFormatOperator.EQUALS:
        return candidate == expected
    if operator is CustomFormatOperator.CONTAINS:
        if isinstance(candidate, str) and isinstance(expected, str):
            return expected.casefold() in candidate.casefold()
        if isinstance(candidate, (list, tuple, set, frozenset)):
            return expected in candidate
        return False
    if operator is CustomFormatOperator.GREATER_THAN:
        return _is_number(candidate) and _is_number(expected) and candidate > expected
    if operator is CustomFormatOperator.LESS_THAN:
        return _is_number(candidate) and _is_number(expected) and candidate < expected
    return False


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
