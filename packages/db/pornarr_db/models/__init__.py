"""Model modules.

Every model module must be imported here. Alembic autogenerate only sees what is
registered on the metadata, and a model that is never imported produces an empty
migration with no error — the failure is silent, which is why the import list is
explicit rather than a directory scan.
"""

from __future__ import annotations

__all__: list[str] = []
