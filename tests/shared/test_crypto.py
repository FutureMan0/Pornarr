from __future__ import annotations

import pytest

from pornarr_shared.crypto import VERSION_PREFIX, CredentialCipher, derive_key
from pornarr_shared.errors import DecryptionError

SECRET = "0123456789abcdef0123456789abcdef"
OTHER_SECRET = "fedcba9876543210fedcba9876543210"


def test_roundtrip() -> None:
    cipher = CredentialCipher(SECRET)
    assert cipher.decrypt(cipher.encrypt("hunter2")) == "hunter2"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    cipher = CredentialCipher(SECRET)
    plaintext = "an-indexer-api-key"
    assert plaintext not in cipher.encrypt(plaintext)


def test_same_plaintext_encrypts_differently_each_time() -> None:
    """Fernet includes a random IV. Identical ciphertexts would let an observer
    tell that two indexers share an API key just by reading the table."""
    cipher = CredentialCipher(SECRET)
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_a_different_secret_cannot_decrypt() -> None:
    written = CredentialCipher(SECRET).encrypt("hunter2")

    with pytest.raises(DecryptionError) as caught:
        CredentialCipher(OTHER_SECRET).decrypt(written)

    assert "APP_SECRET" in caught.value.message


def test_plaintext_in_the_column_is_reported_rather_than_returned() -> None:
    with pytest.raises(DecryptionError, match="version prefix"):
        CredentialCipher(SECRET).decrypt("not-encrypted-at-all")


def test_ciphertext_carries_a_version_prefix() -> None:
    assert CredentialCipher(SECRET).encrypt("x").startswith(VERSION_PREFIX)


def test_key_derivation_is_deterministic_and_secret_specific() -> None:
    assert derive_key(SECRET) == derive_key(SECRET)
    assert derive_key(SECRET) != derive_key(OTHER_SECRET)


def test_unicode_survives_the_roundtrip() -> None:
    cipher = CredentialCipher(SECRET)
    value = "pässwörd-🔐-日本語"
    assert cipher.decrypt(cipher.encrypt(value)) == value


def test_empty_value_roundtrips() -> None:
    cipher = CredentialCipher(SECRET)
    assert cipher.decrypt(cipher.encrypt("")) == ""
