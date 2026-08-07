"""Write the API contract to stdout.

The output must be byte-identical between runs on unchanged code, because CI
compares it against the committed file. Anything unstable — dict ordering, a
timestamp, a generated name that depends on import order — turns the drift check
into a source of false failures that people then learn to ignore.

Usage: python -m pornarr_api.scripts.export_openapi > openapi.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import SecretStr

from pornarr_api.main import create_app
from pornarr_shared.config import Settings


def _export_settings() -> Settings:
    """Settings that never touch the environment.

    The schema must not depend on how the machine running the export happens to
    be configured, or two developers produce two different files.
    """
    return Settings(
        app_secret=SecretStr("0" * 32),
        database_url="postgresql+psycopg://export:export@localhost:5432/export",
        redis_url="redis://localhost:6379/0",
        app_env="development",
        base_path="",
    )


def build_schema() -> dict[str, Any]:
    app = create_app(_export_settings())
    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    # sort_keys makes the output independent of insertion order; the trailing
    # newline keeps `diff` and every editor happy.
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    sys.stdout.write(render(build_schema()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
