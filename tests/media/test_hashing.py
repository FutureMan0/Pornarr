from __future__ import annotations

from pathlib import Path

from pornarr_media.hashing import SAMPLE_SIZE, oshash


def test_matches_the_opensubtitles_reference_algorithm(tmp_path: Path) -> None:
    path = tmp_path / "reference.bin"
    path.write_bytes(bytes(range(256)) * (SAMPLE_SIZE // 256 * 2))

    assert oshash(path) == "a0601fdf9f610000"


def test_small_file_returns_no_hash(tmp_path: Path) -> None:
    path = tmp_path / "small.bin"
    path.write_bytes(b"x" * (SAMPLE_SIZE - 1))

    assert oshash(path) is None


# --- The fingerprint's shape (docs/pipelines/import.md step 2) ----------------
#
# "oshash over the first and last 64 KB plus file size." Each test below moves
# exactly one of those three inputs, so a change of hash names which one moved,
# and the middle-of-the-file test proves the other bytes are never read.

MIDDLE_SIZE = 4 * SAMPLE_SIZE


def _sampled_file(path: Path, *, first: int, middle: int, last: int, middle_size: int) -> Path:
    path.write_bytes(
        bytes([first]) * SAMPLE_SIZE + bytes([middle]) * middle_size + bytes([last]) * SAMPLE_SIZE
    )
    return path


def test_only_the_first_and_last_sample_and_the_size_reach_the_hash(tmp_path: Path) -> None:
    baseline = _sampled_file(
        tmp_path / "baseline.bin", first=1, middle=2, last=3, middle_size=MIDDLE_SIZE
    )
    same_ends = _sampled_file(
        tmp_path / "same-ends.bin", first=1, middle=9, last=3, middle_size=MIDDLE_SIZE
    )

    # Every byte between the two samples differs, and the fingerprint does not.
    assert oshash(same_ends) == oshash(baseline)
    assert same_ends.read_bytes() != baseline.read_bytes()


def test_a_different_first_sample_a_different_last_sample_or_a_different_size_all_change_it(
    tmp_path: Path,
) -> None:
    baseline = oshash(
        _sampled_file(tmp_path / "baseline.bin", first=1, middle=2, last=3, middle_size=MIDDLE_SIZE)
    )
    first_changed = oshash(
        _sampled_file(tmp_path / "first.bin", first=4, middle=2, last=3, middle_size=MIDDLE_SIZE)
    )
    last_changed = oshash(
        _sampled_file(tmp_path / "last.bin", first=1, middle=2, last=7, middle_size=MIDDLE_SIZE)
    )
    size_changed = oshash(
        _sampled_file(tmp_path / "size.bin", first=1, middle=2, last=3, middle_size=MIDDLE_SIZE + 8)
    )

    assert len({baseline, first_changed, last_changed, size_changed}) == 4


def test_the_hash_is_a_sixteen_digit_hexadecimal_value_and_is_stable(tmp_path: Path) -> None:
    path = _sampled_file(
        tmp_path / "stable.bin", first=1, middle=2, last=3, middle_size=MIDDLE_SIZE
    )

    first_call = oshash(path)

    assert first_call is not None
    assert len(first_call) == 16
    assert int(first_call, 16) >= 0
    assert oshash(path) == first_call


def test_a_file_of_exactly_one_sample_is_hashed_and_one_byte_less_is_not(tmp_path: Path) -> None:
    """The boundary the implementation guards, from both sides."""
    exact = tmp_path / "exact.bin"
    short = tmp_path / "short.bin"
    exact.write_bytes(b"\x01" * SAMPLE_SIZE)
    short.write_bytes(b"\x01" * (SAMPLE_SIZE - 1))

    assert oshash(exact) is not None
    assert oshash(short) is None
