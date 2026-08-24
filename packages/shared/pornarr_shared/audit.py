"""Shared audit vocabulary without a persistence dependency."""

from __future__ import annotations

from enum import StrEnum


class AuditSource(StrEnum):
    SESSION = "session"
    API_KEY = "apikey"
    AUTOMATION = "automation"
