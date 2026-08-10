from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import pornarr_worker.artwork as worker_artwork
from pornarr_media.artwork import Artwork, ArtworkState
from pornarr_worker.artwork import generate_artwork_job, regenerate_library_artwork_job


async def test_artwork_job_generates_one_item_in_the_background(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    poster = tmp_path / "poster.jpg"
    monkeypatch.setattr(
        worker_artwork,
        "generate_artwork",
        lambda *_args, **_kwargs: Artwork(ArtworkState.READY, poster, ()),
    )

    generated = await generate_artwork_job(
        {}, str(tmp_path / "source.mkv"), str(tmp_path / "thumbnails"), str(uuid4())
    )

    assert generated is True


async def test_library_artwork_job_regenerates_each_requested_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        worker_artwork,
        "generate_artwork",
        lambda *_args, **_kwargs: (
            calls.append(object()) or Artwork(ArtworkState.READY, tmp_path, ())
        ),
    )
    items = [
        {"source_path": str(tmp_path / "one.mkv"), "media_id": str(uuid4())},
        {"source_path": str(tmp_path / "two.mkv"), "media_id": str(uuid4())},
    ]

    generated = await regenerate_library_artwork_job({}, str(tmp_path / "thumbnails"), items)

    assert generated == 2
    assert len(calls) == 2
