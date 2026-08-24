"""Administrator-managed quality profiles and custom release-format scoring."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, JsonValue, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pornarr_api.auth import database_session, require_role
from pornarr_core.matching import ParsedRelease, parse_release
from pornarr_core.quality import QualityProfile as CoreQualityProfile
from pornarr_core.quality import ReleaseCandidate, decide_quality
from pornarr_db.audit import write_audit
from pornarr_db.custom_formats import matches_custom_format
from pornarr_db.models.custom_formats import (
    CustomFormat,
    CustomFormatCondition,
    CustomFormatField,
    CustomFormatOperator,
)
from pornarr_db.models.monitor import Monitor
from pornarr_db.models.quality import QualityDefinition, QualityProfile, QualityProfileItem
from pornarr_db.models.user import User, UserRole
from pornarr_shared.errors import PornarrError

router = APIRouter(prefix="/admin/quality", tags=["admin"])
Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

_PROFILE_OPTIONS = (
    selectinload(QualityProfile.cutoff_quality),
    selectinload(QualityProfile.items).selectinload(QualityProfileItem.quality_definition),
)
_RELEASE_FLAGS = ("proper", "repack", "remux")


class QualityConfigurationError(PornarrError):
    code = "QUALITY_CONFIGURATION_INVALID"
    status = 422


class QualityProfileInUseError(PornarrError):
    code = "QUALITY_PROFILE_IN_USE"
    status = 409


class QualityDefinitionResponse(BaseModel):
    id: UUID
    name: str
    resolution: str
    source: str
    weight: int
    minimum_size_mb_per_minute: float
    maximum_size_mb_per_minute: float


class QualityProfileFields(BaseModel):
    quality_definition_ids: Annotated[list[UUID], Field(min_length=1, max_length=64)]
    cutoff_quality_id: UUID
    minimum_custom_format_score: int = 0

    @field_validator("quality_definition_ids")
    @classmethod
    def require_unique_qualities(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("quality_definition_ids must not contain duplicates")
        return values


class QualityProfileWrite(QualityProfileFields):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class QualityProfilePreview(QualityProfileFields):
    pass


class QualityProfileResponse(BaseModel):
    id: UUID
    name: str
    cutoff_quality_id: UUID
    minimum_custom_format_score: int
    is_default: bool
    qualities: list[QualityDefinitionResponse]


class CustomFormatConditionWrite(BaseModel):
    field: CustomFormatField
    operator: CustomFormatOperator
    value: JsonValue
    negate: bool = False
    required: bool = True


class CustomFormatConditionResponse(CustomFormatConditionWrite):
    id: UUID


class CustomFormatWrite(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    score: int = 0
    conditions: Annotated[list[CustomFormatConditionWrite], Field(min_length=1, max_length=32)]

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class CustomFormatResponse(BaseModel):
    id: UUID
    name: str
    score: int
    conditions: list[CustomFormatConditionResponse]


class QualityPreviewRequest(BaseModel):
    release_name: Annotated[str, Field(min_length=1, max_length=1024)]
    profile: QualityProfilePreview
    custom_formats: Annotated[list[CustomFormatWrite], Field(max_length=64)] = []

    @field_validator("release_name")
    @classmethod
    def strip_release_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("release_name must not be blank")
        return stripped


class CustomFormatScore(BaseModel):
    name: str
    score: int


class QualityPreviewResponse(BaseModel):
    quality: QualityDefinitionResponse | None
    score: int
    verdict: str
    reason: str
    matched_custom_formats: list[CustomFormatScore]
    fields: dict[str, JsonValue]


@router.get("/definitions", response_model=list[QualityDefinitionResponse])
async def list_quality_definitions(_: Admin, session: Session) -> list[QualityDefinitionResponse]:
    definitions = await session.scalars(
        select(QualityDefinition).order_by(QualityDefinition.weight, QualityDefinition.name)
    )
    return [_definition_response(definition) for definition in definitions]


@router.get("/profiles", response_model=list[QualityProfileResponse])
async def list_quality_profiles(_: Admin, session: Session) -> list[QualityProfileResponse]:
    profiles = await session.scalars(
        select(QualityProfile).options(*_PROFILE_OPTIONS).order_by(QualityProfile.name)
    )
    return [_profile_response(profile) for profile in profiles]


@router.post("/profiles", response_model=QualityProfileResponse, status_code=201)
async def create_quality_profile(
    payload: QualityProfileWrite, user: Admin, session: Session
) -> QualityProfileResponse:
    await _ensure_profile_name_available(session, payload.name)
    definitions = await _definitions_for(session, payload.quality_definition_ids)
    _validate_cutoff(payload, definitions)
    profile = QualityProfile(
        name=payload.name,
        cutoff_quality_id=payload.cutoff_quality_id,
        minimum_custom_format_score=payload.minimum_custom_format_score,
        is_default=False,
    )
    profile.items = _profile_items(payload.quality_definition_ids, definitions)
    if payload.is_default:
        await _clear_default_profile(session)
        profile.is_default = True
    session.add(profile)
    await session.flush()
    write_audit(session, actor_id=user.id, action="quality_profile.created", target=str(profile.id))
    return _profile_response(profile)


@router.get("/profiles/{profile_id}", response_model=QualityProfileResponse)
async def read_quality_profile(
    profile_id: UUID, _: Admin, session: Session
) -> QualityProfileResponse:
    return _profile_response(await _profile_or_404(session, profile_id))


@router.put("/profiles/{profile_id}", response_model=QualityProfileResponse)
async def update_quality_profile(
    profile_id: UUID, payload: QualityProfileWrite, user: Admin, session: Session
) -> QualityProfileResponse:
    profile = await _profile_or_404(session, profile_id)
    await _ensure_profile_name_available(session, payload.name, excluded_id=profile.id)
    definitions = await _definitions_for(session, payload.quality_definition_ids)
    _validate_cutoff(payload, definitions)
    if profile.is_default and not payload.is_default:
        raise QualityConfigurationError(
            "At least one quality profile must remain the default.", reason="default_required"
        )
    if payload.is_default:
        await _clear_default_profile(session, excluded_id=profile.id)
    profile.name = payload.name
    profile.cutoff_quality_id = payload.cutoff_quality_id
    profile.minimum_custom_format_score = payload.minimum_custom_format_score
    profile.is_default = payload.is_default
    profile.items.clear()
    await session.flush()
    profile.items = _profile_items(payload.quality_definition_ids, definitions)
    await session.flush()
    write_audit(session, actor_id=user.id, action="quality_profile.updated", target=str(profile.id))
    return _profile_response(profile)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_quality_profile(profile_id: UUID, user: Admin, session: Session) -> None:
    profile = await _profile_or_404(session, profile_id)
    if profile.is_default:
        raise QualityProfileInUseError(
            "The default quality profile cannot be deleted.", reason="default"
        )
    if await session.scalar(
        select(Monitor.id).where(Monitor.quality_profile_id == profile.id).limit(1)
    ):
        raise QualityProfileInUseError(
            "A monitor still uses this quality profile.", reason="monitor"
        )
    await session.delete(profile)
    write_audit(session, actor_id=user.id, action="quality_profile.deleted", target=str(profile_id))


@router.get("/custom-formats", response_model=list[CustomFormatResponse])
async def list_custom_formats(_: Admin, session: Session) -> list[CustomFormatResponse]:
    formats = await session.scalars(
        select(CustomFormat)
        .options(selectinload(CustomFormat.conditions))
        .order_by(CustomFormat.name)
    )
    return [_custom_format_response(format_) for format_ in formats]


@router.post("/custom-formats", response_model=CustomFormatResponse, status_code=201)
async def create_custom_format(
    payload: CustomFormatWrite, user: Admin, session: Session
) -> CustomFormatResponse:
    await _ensure_custom_format_name_available(session, payload.name)
    format_ = _custom_format_from_payload(payload)
    session.add(format_)
    await session.flush()
    write_audit(session, actor_id=user.id, action="custom_format.created", target=str(format_.id))
    return _custom_format_response(format_)


@router.put("/custom-formats/{format_id}", response_model=CustomFormatResponse)
async def update_custom_format(
    format_id: UUID, payload: CustomFormatWrite, user: Admin, session: Session
) -> CustomFormatResponse:
    format_ = await _custom_format_or_404(session, format_id)
    await _ensure_custom_format_name_available(session, payload.name, excluded_id=format_.id)
    format_.name = payload.name
    format_.score = payload.score
    format_.conditions.clear()
    await session.flush()
    format_.conditions = _conditions_from_payload(payload.conditions)
    await session.flush()
    write_audit(session, actor_id=user.id, action="custom_format.updated", target=str(format_.id))
    return _custom_format_response(format_)


@router.delete("/custom-formats/{format_id}", status_code=204)
async def delete_custom_format(format_id: UUID, user: Admin, session: Session) -> None:
    format_ = await _custom_format_or_404(session, format_id)
    await session.delete(format_)
    write_audit(session, actor_id=user.id, action="custom_format.deleted", target=str(format_id))


@router.post("/preview", response_model=QualityPreviewResponse)
async def preview_quality_decision(
    payload: QualityPreviewRequest, _: Admin, session: Session
) -> QualityPreviewResponse:
    definitions = await _definitions_for(session, payload.profile.quality_definition_ids)
    _validate_cutoff(payload.profile, definitions)
    parsed = parse_release(payload.release_name)
    fields = _release_fields(parsed)
    formats = [_custom_format_from_payload(format_) for format_ in payload.custom_formats]
    matched_formats = [format_ for format_ in formats if matches_custom_format(format_, fields)]
    matched_scores = [format_.score for format_ in matched_formats]
    quality = _quality_from_release(parsed, definitions.values())
    response_fields = {key: value for key, value in fields.items() if key != "title"}
    if quality is None:
        return QualityPreviewResponse(
            quality=None,
            score=sum(matched_scores),
            verdict="reject",
            reason="quality_not_detected",
            matched_custom_formats=[
                CustomFormatScore(name=format_.name, score=format_.score)
                for format_ in matched_formats
            ],
            fields=response_fields,
        )
    decision = decide_quality(
        ReleaseCandidate(
            quality=quality.name,
            quality_rank=quality.weight,
            custom_format_scores=tuple(matched_scores),
        ),
        CoreQualityProfile(
            allowed_qualities=frozenset(definition.name for definition in definitions.values()),
            cutoff_quality_rank=definitions[payload.profile.cutoff_quality_id].weight,
            minimum_custom_format_score=payload.profile.minimum_custom_format_score,
        ),
        existing=None,
    )
    return QualityPreviewResponse(
        quality=_definition_response(quality),
        score=decision.score,
        verdict=decision.verdict.value,
        reason=decision.reason.value,
        matched_custom_formats=[
            CustomFormatScore(name=format_.name, score=format_.score) for format_ in matched_formats
        ],
        fields=response_fields,
    )


def _definition_response(definition: QualityDefinition) -> QualityDefinitionResponse:
    return QualityDefinitionResponse(
        id=definition.id,
        name=definition.name,
        resolution=definition.resolution,
        source=definition.source,
        weight=definition.weight,
        minimum_size_mb_per_minute=definition.minimum_size_mb_per_minute,
        maximum_size_mb_per_minute=definition.maximum_size_mb_per_minute,
    )


def _profile_response(profile: QualityProfile) -> QualityProfileResponse:
    return QualityProfileResponse(
        id=profile.id,
        name=profile.name,
        cutoff_quality_id=profile.cutoff_quality_id,
        minimum_custom_format_score=profile.minimum_custom_format_score,
        is_default=profile.is_default,
        qualities=[_definition_response(item.quality_definition) for item in profile.items],
    )


def _custom_format_response(format_: CustomFormat) -> CustomFormatResponse:
    return CustomFormatResponse(
        id=format_.id,
        name=format_.name,
        score=format_.score,
        conditions=[
            CustomFormatConditionResponse(
                id=condition.id,
                field=condition.field,
                operator=condition.operator,
                value=cast(Any, condition.value),
                negate=condition.negate,
                required=condition.required,
            )
            for condition in format_.conditions
        ],
    )


async def _profile_or_404(session: AsyncSession, profile_id: UUID) -> QualityProfile:
    profile = await session.scalar(
        select(QualityProfile).options(*_PROFILE_OPTIONS).where(QualityProfile.id == profile_id)
    )
    if profile is None:
        raise HTTPException(status_code=404)
    return profile


async def _custom_format_or_404(session: AsyncSession, format_id: UUID) -> CustomFormat:
    format_ = await session.scalar(
        select(CustomFormat)
        .options(selectinload(CustomFormat.conditions))
        .where(CustomFormat.id == format_id)
    )
    if format_ is None:
        raise HTTPException(status_code=404)
    return format_


async def _definitions_for(
    session: AsyncSession, definition_ids: Sequence[UUID]
) -> dict[UUID, QualityDefinition]:
    definitions = list(
        await session.scalars(
            select(QualityDefinition).where(QualityDefinition.id.in_(definition_ids))
        )
    )
    by_id = {definition.id: definition for definition in definitions}
    if len(by_id) != len(definition_ids):
        raise QualityConfigurationError("A selected quality definition does not exist.")
    return by_id


def _validate_cutoff(
    payload: QualityProfileFields, definitions: dict[UUID, QualityDefinition]
) -> None:
    if payload.cutoff_quality_id not in definitions:
        raise QualityConfigurationError("The cutoff quality must be in the profile.")


async def _ensure_profile_name_available(
    session: AsyncSession, name: str, *, excluded_id: UUID | None = None
) -> None:
    statement = select(QualityProfile.id).where(QualityProfile.name == name)
    if excluded_id is not None:
        statement = statement.where(QualityProfile.id != excluded_id)
    if await session.scalar(statement.limit(1)) is not None:
        raise QualityConfigurationError(
            "A quality profile already uses that name.", reason="duplicate_name"
        )


async def _ensure_custom_format_name_available(
    session: AsyncSession, name: str, *, excluded_id: UUID | None = None
) -> None:
    statement = select(CustomFormat.id).where(CustomFormat.name == name)
    if excluded_id is not None:
        statement = statement.where(CustomFormat.id != excluded_id)
    if await session.scalar(statement.limit(1)) is not None:
        raise QualityConfigurationError(
            "A custom format already uses that name.", reason="duplicate_name"
        )


async def _clear_default_profile(session: AsyncSession, *, excluded_id: UUID | None = None) -> None:
    statement = select(QualityProfile).where(QualityProfile.is_default.is_(True))
    if excluded_id is not None:
        statement = statement.where(QualityProfile.id != excluded_id)
    current_default = await session.scalar(statement)
    if current_default is not None:
        current_default.is_default = False
        await session.flush()


def _profile_items(
    definition_ids: Sequence[UUID], definitions: dict[UUID, QualityDefinition]
) -> list[QualityProfileItem]:
    return [
        QualityProfileItem(quality_definition=definitions[definition_id], position=position)
        for position, definition_id in enumerate(definition_ids)
    ]


def _custom_format_from_payload(payload: CustomFormatWrite) -> CustomFormat:
    return CustomFormat(
        name=payload.name,
        score=payload.score,
        conditions=_conditions_from_payload(payload.conditions),
    )


def _conditions_from_payload(
    conditions: Sequence[CustomFormatConditionWrite],
) -> list[CustomFormatCondition]:
    return [
        CustomFormatCondition(
            field=condition.field,
            operator=condition.operator,
            value=cast(Any, condition.value),
            negate=condition.negate,
            required=condition.required,
        )
        for condition in conditions
    ]


def _release_fields(parsed: ParsedRelease) -> dict[str, JsonValue]:
    fields: dict[str, JsonValue] = {"title": parsed.title}
    if parsed.resolution is not None:
        fields["resolution"] = parsed.resolution
    if parsed.source is not None:
        fields["source"] = parsed.source
    if parsed.codec is not None:
        fields["codec"] = parsed.codec
    flags = [
        flag for flag in _RELEASE_FLAGS if re.search(rf"\b{flag}\b", parsed.title, re.IGNORECASE)
    ]
    if flags:
        fields["flags"] = flags
    return fields


def _quality_from_release(
    parsed: ParsedRelease, definitions: Iterable[QualityDefinition]
) -> QualityDefinition | None:
    if parsed.resolution is None or parsed.source is None:
        return None
    normalized_source = _normalized_source(parsed.source)
    return next(
        (
            definition
            for definition in sorted(definitions, key=lambda item: item.weight, reverse=True)
            if definition.resolution.casefold() == parsed.resolution.casefold()
            and normalized_source.startswith(_normalized_source(definition.source))
        ),
        None,
    )


def _normalized_source(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
