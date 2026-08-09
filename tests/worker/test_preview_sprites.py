from __future__ import annotations

from pathlib import Path

import pytest

import pornarr_worker.sprites as worker_sprites
from pornarr_worker.sprites import generate_preview_sprite_job


async def test_sprite_job_runs_generation_off_the_import_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[Path, Path, object]] = []

    def generate(source: Path, output_directory: Path, *, options: object) -> object:
        calls.append((source, output_directory, options))
        return object()

    monkeypatch.setattr(worker_sprites, "try_generate_preview_sprite", generate)

    generated = await generate_preview_sprite_job(
        {},
        str(tmp_path / "source.mkv"),
        str(tmp_path / "previews"),
        interval_seconds=5,
        tile_width=120,
        tile_height=68,
        columns=4,
    )

    assert generated is True
    assert calls[0][:2] == (tmp_path / "source.mkv", tmp_path / "previews")


async def test_sprite_job_treats_generation_failure_as_non_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        worker_sprites, "try_generate_preview_sprite", lambda *_args, **_kwargs: None
    )

    generated = await generate_preview_sprite_job({}, str(tmp_path / "source.mkv"), str(tmp_path))

    assert generated is False
