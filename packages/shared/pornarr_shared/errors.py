"""Error types shared across the application.

Errors carry a stable machine-readable code rather than a message, because the
frontend translates codes and must never render a server-supplied English
string. See docs/api-contract.md.
"""

from __future__ import annotations

from typing import Any


class PornarrError(Exception):
    """Base for every error this application raises deliberately.

    `code` is part of the API contract: it appears in responses, is mapped to a
    translated message in the frontend, and changing one is a contract change.
    """

    code: str = "INTERNAL_ERROR"
    status: int = 500

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "status": self.status, "context": self.context}


class ConfigurationError(PornarrError):
    """The instance cannot start with the configuration it was given.

    Raised at startup only. A running instance never produces this, which is why
    it is allowed to be fatal rather than handled.
    """

    code = "CONFIGURATION_INVALID"
    status = 500


class DecryptionError(PornarrError):
    """A stored credential could not be decrypted.

    In practice this almost always means APP_SECRET does not match the one the
    value was encrypted with — a database restored without its secret. The
    message says so, because the alternative is an operator reconfiguring every
    integration by hand without knowing why.
    """

    code = "DECRYPTION_FAILED"
    status = 500
