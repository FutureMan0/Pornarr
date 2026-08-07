"""Column types.

`EncryptedString` exists so that encryption is a property of the column rather
than something every call site has to remember. A credential column that someone
forgot to wrap is indistinguishable from one that works, right up until the
database is read by someone else.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from pornarr_shared.config import get_settings
from pornarr_shared.crypto import CredentialCipher

# Fernet output is base64 and roughly doubles the input, plus the version prefix.
# 1024 covers every credential these integrations actually use.
ENCRYPTED_LENGTH = 1024

_cipher: CredentialCipher | None = None


def get_cipher() -> CredentialCipher:
    """Process-wide cipher. Key derivation is not repeated per value."""
    global _cipher
    if _cipher is None:
        _cipher = CredentialCipher(get_settings().app_secret.get_secret_value())
    return _cipher


def set_cipher(cipher: CredentialCipher | None) -> None:
    """Override the process cipher. For tests, which must not need real settings."""
    global _cipher
    _cipher = cipher


class EncryptedString(TypeDecorator[str]):
    """A string column that is encrypted at rest.

    Reads and writes look like an ordinary string to the model; the value in the
    database is ciphertext and is unreadable without `APP_SECRET`.
    """

    impl = String(ENCRYPTED_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return get_cipher().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return get_cipher().decrypt(value)

    def __repr__(self) -> str:
        return "EncryptedString()"

    # A comparison against the plaintext cannot work: every encryption produces a
    # different ciphertext, so `WHERE api_key = 'x'` would silently match nothing.
    # Raising is better than returning an empty result set for ever.
    class comparator_factory(TypeDecorator.Comparator[str]):  # noqa: N801 - SQLAlchemy hook
        def __eq__(self, other: Any) -> Any:
            message = (
                "An encrypted column cannot be compared in SQL: every write produces "
                "different ciphertext, so the comparison would silently match nothing. "
                "Load the row and compare in Python, or store a separate lookup key."
            )
            raise NotImplementedError(message)

        def __ne__(self, other: Any) -> Any:
            return self.__eq__(other)

        __hash__ = None  # type: ignore[assignment]
