from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pornarr_media.probe import MediaProbeError, MediaProbeTimeoutError, probe


def test_parses_video_and_audio_streams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = """{"format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2","duration":"12.5","bit_rate":"1000000"},"streams":[{"codec_type":"video","codec_name":"h264","width":1920,"height":1080},{"codec_type":"audio","codec_name":"aac"}]}"""
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, payload, "")
    )

    result = probe(tmp_path / "sample.mp4")

    assert result.resolution == "1920x1080"
    assert result.codecs == ("h264", "aac")
    assert result.duration == 12.5
    assert result.bitrate == 1_000_000
    assert result.container == "mov,mp4,m4a,3gp,3g2,mj2"


def test_corrupt_file_is_an_explicit_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "Invalid data"),
    )

    with pytest.raises(MediaProbeError, match="could not be probed"):
        probe(tmp_path / "corrupt.mp4")


def test_hanging_probe_is_reported_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("ffprobe", 1)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(MediaProbeTimeoutError, match="timed out"):
        probe(tmp_path / "hanging.mp4", timeout=1)


def test_malformed_probe_output_is_an_explicit_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "{", "")
    )

    with pytest.raises(MediaProbeError, match="could not be probed"):
        probe(tmp_path / "broken.mp4")


def test_missing_or_invalid_optional_metadata_stays_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = """{"format":{"duration":"invalid","bit_rate":"invalid"},"streams":[{"codec_type":"audio","codec_name":"opus"}]}"""
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, payload, "")
    )

    result = probe(tmp_path / "audio.webm")

    assert result.resolution is None
    assert result.duration is None
    assert result.bitrate is None
    assert result.container is None


def test_non_scalar_optional_metadata_stays_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = """{"format":{"duration":[],"bit_rate":{}},"streams":[]}"""
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, payload, "")
    )

    result = probe(tmp_path / "unknown.mkv")

    assert result.duration is None
    assert result.bitrate is None
