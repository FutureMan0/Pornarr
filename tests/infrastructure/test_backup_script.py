"""The backup command must produce a restorable dump without exporting secrets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BACKUP_SCRIPT = Path(__file__).parents[2] / "infrastructure/scripts/backup.sh"


def test_backup_script_creates_a_dump_and_secret_free_config(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "APP_SECRET=secret\n"
        "DATABASE_URL=postgresql://pornarr:password@database/pornarr\n"
        "REDIS_URL=redis://:password@redis\n"
        "INDEXER_API_KEY=api-key\n"
        "DATA_PATH=/data\n"
        "LOG_LEVEL=info\n"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$*" > "$DOCKER_LOG"\n'
        'printf "database dump"\n'
    )
    (fake_bin / "docker").chmod(0o755)
    backup_dir = tmp_path / "backups"
    environment = os.environ | {
        "BACKUP_DIR": str(backup_dir),
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    subprocess.run(["bash", str(BACKUP_SCRIPT)], cwd=tmp_path, env=environment, check=True)

    assert (tmp_path / "docker.log").read_text().strip() == (
        "compose exec -T postgres pg_dump -U pornarr -Fc pornarr"
    )
    assert next(backup_dir.glob("*.dump")).read_text() == "database dump"
    config = next(backup_dir.glob("*.env")).read_text()
    assert "APP_SECRET" not in config
    assert "DATABASE_URL" not in config
    assert "REDIS_URL" not in config
    assert "INDEXER_API_KEY" not in config
    assert "DATA_PATH=/data" in config
    assert "LOG_LEVEL=info" in config
