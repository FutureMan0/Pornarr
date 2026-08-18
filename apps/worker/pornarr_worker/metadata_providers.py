"""Build metadata adapters from the keys an operator configured.

The cascade takes adapters; the administrator stores keys. This is the one
place that turns the second into the first, so a provider added in settings is
used by the next import without a restart.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.metadata_provider import MetadataProvider
from pornarr_integrations.metadata import MetadataProviderAdapter
from pornarr_integrations.metadata.stashdb import StashdbAdapter
from pornarr_integrations.metadata.tpdb import TpdbAdapter

STASHDB_ENDPOINT = "https://stashdb.org/graphql"


async def configured_providers(session: AsyncSession) -> tuple[MetadataProviderAdapter, ...]:
    """Return the enabled providers in the order the cascade should ask them."""

    providers = await session.scalars(
        select(MetadataProvider)
        .where(MetadataProvider.enabled.is_(True))
        .order_by(MetadataProvider.priority, MetadataProvider.implementation)
    )
    adapters = [_adapter(provider) for provider in providers]
    return tuple(adapter for adapter in adapters if adapter is not None)


def _adapter(provider: MetadataProvider) -> MetadataProviderAdapter | None:
    if provider.implementation == "stashdb":
        return StashdbAdapter.from_configuration(
            endpoint=provider.endpoint or STASHDB_ENDPOINT, api_key=provider.api_key
        )
    if provider.implementation == "tpdb":
        return TpdbAdapter.from_configuration(endpoint=provider.endpoint, api_key=provider.api_key)
    # A row for an implementation this build does not carry is configuration
    # for a future version, not a reason to fail every import.
    return None
