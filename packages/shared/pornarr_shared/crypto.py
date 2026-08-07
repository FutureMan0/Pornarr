"""Credential encryption.

Indexer API keys, download client passwords, metadata provider keys and OIDC
client secrets are encrypted at rest with a key derived from `APP_SECRET`. No
read endpoint ever returns them; the UI shows whether a secret is set, not what
it is.

Losing `APP_SECRET` means losing every stored credential — which is why
docs/operations/backup.md treats it as a first-class backup artefact.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pornarr_shared.errors import DecryptionError

# Prefixing the ciphertext means a future key rotation can tell old values from
# new ones without a migration that has to decrypt everything first.
VERSION_PREFIX = "v1:"

_HKDF_INFO = b"pornarr-credential-encryption-v1"


def derive_key(secret: str) -> bytes:
    """Derive a Fernet key from the application secret.

    HKDF rather than using the secret directly: `APP_SECRET` is also used for
    other purposes, and a key derived per purpose means compromising one does
    not hand over the others.
    """
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class CredentialCipher:
    """Encrypts and decrypts stored credentials.

    Constructed from the application secret and held for the process lifetime;
    key derivation is deliberately not repeated per value.
    """

    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(derive_key(secret))

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{VERSION_PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(VERSION_PREFIX):
            raise DecryptionError(
                "Stored credential has no version prefix. It was written by a "
                "different version of Pornarr, or the column contains plaintext."
            )
        token = ciphertext.removeprefix(VERSION_PREFIX)
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DecryptionError(
                "Stored credential could not be decrypted. This almost always "
                "means APP_SECRET does not match the value the credential was "
                "encrypted with — for example a database restored without its "
                "secret. Restore the original APP_SECRET, or re-enter every "
                "integration credential."
            ) from exc
