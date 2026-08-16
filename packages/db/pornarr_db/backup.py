"""Database checks that make restored credentials fail early and clearly."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_db.models.settings import Setting
from pornarr_shared.crypto import CredentialCipher
from pornarr_shared.errors import ConfigurationError, DecryptionError

APP_SECRET_CHECK_KEY = "app_secret_check"
_APP_SECRET_CHECK_VALUE = "pornarr-app-secret-check-v1"


async def ensure_app_secret_matches(session: AsyncSession, app_secret: str) -> None:
    """Create or verify the encrypted marker tied to this database's secret."""

    cipher = CredentialCipher(app_secret)
    stored = await session.get(Setting, APP_SECRET_CHECK_KEY)
    if stored is None:
        session.add(
            Setting(key=APP_SECRET_CHECK_KEY, value=cipher.encrypt(_APP_SECRET_CHECK_VALUE))
        )
        await session.flush()
        return

    try:
        matches = (
            isinstance(stored.value, str)
            and cipher.decrypt(stored.value) == _APP_SECRET_CHECK_VALUE
        )
    except DecryptionError as exc:
        raise ConfigurationError(
            "APP_SECRET does not match the database. Restore the APP_SECRET that was "
            "backed up with this database before starting Pornarr."
        ) from exc
    if not matches:
        raise ConfigurationError(
            "APP_SECRET does not match the database. Restore the APP_SECRET that was "
            "backed up with this database before starting Pornarr."
        )
