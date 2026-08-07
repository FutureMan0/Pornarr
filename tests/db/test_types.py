from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.dialects import postgresql

from pornarr_db.types import ENCRYPTED_LENGTH, EncryptedString, set_cipher
from pornarr_shared.crypto import CredentialCipher

SECRET = "0123456789abcdef0123456789abcdef"
DIALECT = postgresql.dialect()


@pytest.fixture(autouse=True)
def _cipher() -> Iterator[None]:
    """Inject a cipher so the type does not need real application settings."""
    set_cipher(CredentialCipher(SECRET))
    yield
    set_cipher(None)


def test_value_is_encrypted_on_the_way_in() -> None:
    column = EncryptedString()
    stored = column.process_bind_param("an-api-key", DIALECT)

    assert stored is not None
    assert "an-api-key" not in stored


def test_value_is_decrypted_on_the_way_out() -> None:
    column = EncryptedString()
    stored = column.process_bind_param("an-api-key", DIALECT)

    assert column.process_result_value(stored, DIALECT) == "an-api-key"


def test_none_passes_through_untouched() -> None:
    """A nullable credential column must stay nullable; encrypting None would
    turn 'not configured' into a value that looks configured."""
    column = EncryptedString()
    assert column.process_bind_param(None, DIALECT) is None
    assert column.process_result_value(None, DIALECT) is None


def test_column_is_wide_enough_for_realistic_credentials() -> None:
    """Fernet is base64 and roughly doubles the input, plus the version prefix."""
    column = EncryptedString()
    longest_realistic = "x" * 256
    stored = column.process_bind_param(longest_realistic, DIALECT)

    assert stored is not None
    assert len(stored) < ENCRYPTED_LENGTH


def test_sql_comparison_is_refused_rather_than_silently_matching_nothing() -> None:
    """Every write produces different ciphertext, so `WHERE key = 'x'` would
    return an empty result set for ever. Failing loudly beats that."""
    from sqlalchemy import Column

    column = Column("api_key", EncryptedString())

    with pytest.raises(NotImplementedError, match="cannot be compared in SQL"):
        _ = column == "plaintext"


def test_type_is_cacheable() -> None:
    """Without cache_ok SQLAlchemy re-compiles every statement using the type."""
    assert EncryptedString.cache_ok is True
