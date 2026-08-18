"""Honest, traceable metadata resolution for import candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_core.matching import parse_release
from pornarr_core.naming import split_release_name
from pornarr_db.models.metadata_match import MetadataMatchLog
from pornarr_db.session import session_scope
from pornarr_integrations.metadata import (
    MetadataCandidate,
    MetadataProviderAdapter,
    MetadataRateLimitError,
)
from pornarr_shared.jobs import job


class MetadataTier(StrEnum):
    FINGERPRINT = "fingerprint"
    FILENAME = "filename"
    FUZZY = "fuzzy"
    SITE_DATE_TITLE = "site_date_title"


CONFIDENCE = {
    MetadataTier.FINGERPRINT: 0.95,
    MetadataTier.SITE_DATE_TITLE: 0.80,
    MetadataTier.FUZZY: 0.55,
    MetadataTier.FILENAME: 0.30,
}
FUZZY_TITLE_MINIMUM = 0.85

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MetadataSubject:
    """Known input metadata for one import before remote enrichment."""

    source_path: str
    title: str
    site: str | None = None
    release_date: date | None = None
    performers: tuple[str, ...] = ()
    oshash: str | None = None
    perceptual_hash: str | None = None

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        oshash: str | None = None,
        perceptual_hash: str | None = None,
    ) -> MetadataSubject:
        parsed = parse_release(path.stem)
        return cls(
            source_path=str(path),
            title=parsed.title,
            release_date=parsed.date,
            performers=parsed.performers,
            oshash=oshash,
            perceptual_hash=perceptual_hash,
        )


@dataclass(frozen=True, slots=True)
class MetadataResolution:
    candidate: MetadataCandidate
    confidence: float
    tier: MetadataTier

    def as_payload(self) -> dict[str, object]:
        return {
            "metadata": _candidate_payload(self.candidate),
            "confidence": self.confidence,
            "tier": self.tier,
        }


async def resolve_metadata_cascade(
    session: AsyncSession,
    subject: MetadataSubject,
    providers: Sequence[MetadataProviderAdapter],
    *,
    import_trigger_id: UUID | None = None,
    sleep: Sleep = asyncio.sleep,
) -> MetadataResolution:
    """Resolve metadata from most to least trustworthy, logging every decision."""
    ordered_providers = _providers_by_precedence(providers)
    if subject.oshash or subject.perceptual_hash:
        for provider in ordered_providers:
            candidate = await _attempt(
                session,
                provider,
                MetadataTier.FINGERPRINT,
                {"oshash": subject.oshash, "perceptual_hash": subject.perceptual_hash},
                lambda provider=provider: provider.find_by_fingerprint(
                    oshash=subject.oshash, perceptual_hash=subject.perceptual_hash
                ),
                import_trigger_id=import_trigger_id,
                sleep=sleep,
            )
            if candidate is not None:
                return MetadataResolution(
                    candidate, CONFIDENCE[MetadataTier.FINGERPRINT], MetadataTier.FINGERPRINT
                )

    if subject.site and subject.release_date and subject.title:
        site = subject.site
        release_date = subject.release_date
        assert site is not None and release_date is not None
        for provider in ordered_providers:
            candidate = await _attempt(
                session,
                provider,
                MetadataTier.SITE_DATE_TITLE,
                {
                    "site": site,
                    "release_date": release_date.isoformat(),
                    "title": subject.title,
                },
                lambda provider=provider: provider.find_by_site_date_title(
                    site=site, release_date=release_date, title=subject.title
                ),
                import_trigger_id=import_trigger_id,
                sleep=sleep,
            )
            if candidate is not None and _is_exact_site_date_title(subject, candidate):
                return MetadataResolution(
                    candidate,
                    CONFIDENCE[MetadataTier.SITE_DATE_TITLE],
                    MetadataTier.SITE_DATE_TITLE,
                )

    if subject.title and subject.performers:
        for provider in ordered_providers:
            candidates = await _attempt(
                session,
                provider,
                MetadataTier.FUZZY,
                {"title": subject.title, "performers": list(subject.performers)},
                lambda provider=provider: provider.search(
                    title=subject.title, performers=subject.performers
                ),
                import_trigger_id=import_trigger_id,
                sleep=sleep,
            )
            match = _best_fuzzy_match(subject, candidates or [])
            if match is not None:
                return MetadataResolution(match, CONFIDENCE[MetadataTier.FUZZY], MetadataTier.FUZZY)

    fallback = _filename_candidate(subject)
    _record(
        session,
        import_trigger_id=import_trigger_id,
        provider="filename",
        tier=MetadataTier.FILENAME,
        outcome="matched",
        query={"source_path": subject.source_path},
        result=_candidate_payload(fallback),
        confidence=CONFIDENCE[MetadataTier.FILENAME],
    )
    return MetadataResolution(fallback, CONFIDENCE[MetadataTier.FILENAME], MetadataTier.FILENAME)


async def resolve_metadata_job(
    context: dict[str, Any],
    source_path: str,
    *,
    import_trigger_id: str | None = None,
    oshash: str | None = None,
    perceptual_hash: str | None = None,
) -> dict[str, object]:
    """Resolve one source; later provider configuration supplies the concrete adapters."""
    adapters = context.get("metadata_providers", ())
    if not isinstance(adapters, Sequence):
        raise TypeError("metadata_providers must be a sequence of provider adapters")
    async with session_scope() as session:
        result = await resolve_metadata_cascade(
            session,
            MetadataSubject.from_path(
                Path(source_path), oshash=oshash, perceptual_hash=perceptual_hash
            ),
            adapters,
            import_trigger_id=UUID(import_trigger_id) if import_trigger_id else None,
        )
    return result.as_payload()


async def _attempt[T](
    session: AsyncSession,
    provider: MetadataProviderAdapter,
    tier: MetadataTier,
    query: dict[str, object],
    operation: Callable[[], Awaitable[T]],
    *,
    import_trigger_id: UUID | None,
    sleep: Sleep,
) -> T | None:
    try:
        result = await operation()
    except MetadataRateLimitError as error:
        _record(
            session,
            import_trigger_id=import_trigger_id,
            provider=provider.name,
            tier=tier,
            outcome="rate_limited",
            query=query,
            detail=f"Retry-After: {error.retry_after_seconds}",
        )
        await sleep(error.retry_after_seconds)
        try:
            result = await operation()
        except MetadataRateLimitError as retry_error:
            _record(
                session,
                import_trigger_id=import_trigger_id,
                provider=provider.name,
                tier=tier,
                outcome="rate_limited",
                query=query,
                detail=f"Retry-After: {retry_error.retry_after_seconds}",
            )
            return None
    except Exception as error:
        _record(
            session,
            import_trigger_id=import_trigger_id,
            provider=provider.name,
            tier=tier,
            outcome="error",
            query=query,
            detail=type(error).__name__,
        )
        raise
    _record(
        session,
        import_trigger_id=import_trigger_id,
        provider=provider.name,
        tier=tier,
        outcome="matched" if result else "miss",
        query=query,
        result=_result_payload(result),
        confidence=CONFIDENCE[tier] if result else None,
    )
    return result


def _best_fuzzy_match(
    subject: MetadataSubject, candidates: Sequence[MetadataCandidate]
) -> MetadataCandidate | None:
    matching = [
        candidate
        for candidate in candidates
        if _title_similarity(subject.title, candidate.title) >= FUZZY_TITLE_MINIMUM
        and {name.casefold() for name in subject.performers}
        & {name.casefold() for name in candidate.performers}
    ]
    if not matching:
        return None
    return max(matching, key=lambda candidate: _title_similarity(subject.title, candidate.title))


def _providers_by_precedence(
    providers: Sequence[MetadataProviderAdapter],
) -> tuple[MetadataProviderAdapter, ...]:
    return tuple(
        provider
        for _, provider in sorted(
            enumerate(providers), key=lambda item: (_provider_precedence(item[1]), item[0])
        )
    )


def _provider_precedence(provider: MetadataProviderAdapter) -> int:
    precedence = getattr(provider, "precedence", 100)
    return precedence if isinstance(precedence, int) else 100


def _is_exact_site_date_title(subject: MetadataSubject, candidate: MetadataCandidate) -> bool:
    return (
        candidate.site is not None
        and subject.site is not None
        and candidate.site.casefold() == subject.site.casefold()
        and candidate.release_date == subject.release_date
        and _normalise(candidate.title) == _normalise(subject.title)
    )


def _filename_candidate(subject: MetadataSubject) -> MetadataCandidate:
    # The file name is all there is at this tier, so it is worth reading
    # properly: the studio a scene release states, and a title without the
    # resolution, source, codec and release group nobody wants to see.
    name = split_release_name(subject.title)
    return MetadataCandidate(
        title=name.title,
        studio=name.studio,
        release_date=subject.release_date,
        performers=subject.performers,
    )


def _record(
    session: AsyncSession,
    *,
    import_trigger_id: UUID | None,
    provider: str,
    tier: MetadataTier,
    outcome: str,
    query: dict[str, object],
    result: dict[str, object] | None = None,
    confidence: float | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        MetadataMatchLog(
            import_trigger_id=import_trigger_id,
            provider=provider,
            tier=tier.value,
            outcome=outcome,
            query=query,
            result=result,
            confidence=confidence,
            detail=detail,
        )
    )


def _candidate_payload(candidate: MetadataCandidate) -> dict[str, object]:
    return {
        "title": candidate.title,
        "site": candidate.site,
        "release_date": candidate.release_date.isoformat() if candidate.release_date else None,
        "performers": list(candidate.performers),
        "studio": candidate.studio,
        "tags": list(candidate.tags),
        "provider_id": candidate.provider_id,
    }


def _result_payload(result: object) -> dict[str, object] | None:
    if isinstance(result, MetadataCandidate):
        return _candidate_payload(result)
    if isinstance(result, Sequence):
        return {
            "candidates": [
                _candidate_payload(item) for item in result if isinstance(item, MetadataCandidate)
            ]
        }
    return None


def _title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalise(left), _normalise(right)).ratio()


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


METADATA_RESOLVE_JOB = job(resolve_metadata_job)
