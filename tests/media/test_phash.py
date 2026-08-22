"""Perceptual-hash primitives for non-destructive duplicate detection."""

from __future__ import annotations

from itertools import pairwise

import pytest

from pornarr_media.phash import FRAME_COUNT, frame_offsets, hamming_distance, hash_grayscale_frame


def test_frame_offsets_sample_sixteen_evenly_spaced_interior_positions() -> None:
    offsets = frame_offsets(170)

    assert len(offsets) == FRAME_COUNT
    assert offsets[0] == 10
    assert offsets[-1] == 160
    assert {round(later - earlier, 6) for earlier, later in pairwise(offsets)} == {10.0}


def test_dct_hash_is_stable_for_a_small_luminance_change() -> None:
    pixels = bytes((x * 7 + y * 3) % 256 for y in range(32) for x in range(32))
    brighter = bytes(min(value + 1, 255) for value in pixels)

    assert (
        hamming_distance(
            hash_grayscale_frame(pixels, 32, 32), hash_grayscale_frame(brighter, 32, 32)
        )
        <= 8
    )


def test_hamming_distance_counts_changed_bits() -> None:
    assert hamming_distance(0b0001, 0b1011) == 2


# --- The DCT hash itself -----------------------------------------------------


def _gradient(offset: int = 0) -> bytes:
    return bytes(((x * 7 + y * 3 + offset) % 256) for y in range(32) for x in range(32))


@pytest.mark.parametrize(
    ("what", "duration", "expected_first", "expected_last"),
    [
        ("a three-minute scene", 170.0, 10.0, 160.0),
        ("a seventeen-second short", 17.0, 1.0, 16.0),
        ("a fractional duration", 8.5, 0.5, 8.0),
    ],
)
def test_sixteen_offsets_are_evenly_spaced_and_never_the_first_or_last_frame(
    what: str, duration: float, expected_first: float, expected_last: float
) -> None:
    offsets = frame_offsets(duration)

    assert len(offsets) == FRAME_COUNT, what
    assert offsets[0] == pytest.approx(expected_first), what
    assert offsets[-1] == pytest.approx(expected_last), what
    assert offsets[0] > 0 and offsets[-1] < duration, what
    steps = {round(later - earlier, 9) for earlier, later in pairwise(offsets)}
    assert len(steps) == 1, what


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_a_file_with_no_duration_cannot_be_sampled(duration: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        frame_offsets(duration)


def test_the_hash_is_sixty_four_bits_and_deterministic() -> None:
    first = hash_grayscale_frame(_gradient(), 32, 32)

    assert 0 <= first < 2**64
    assert hash_grayscale_frame(_gradient(), 32, 32) == first


def test_an_unrelated_picture_is_far_beyond_the_duplicate_threshold() -> None:
    gradient = hash_grayscale_frame(_gradient(), 32, 32)
    checkerboard = hash_grayscale_frame(
        bytes(255 if (x // 4 + y // 4) % 2 else 0 for y in range(32) for x in range(32)), 32, 32
    )

    assert hamming_distance(gradient, checkerboard) > 8


@pytest.mark.parametrize(
    ("first", "second", "distance"),
    [
        (0, 0, 0),
        (0b1, 0b0, 1),
        (0b0001, 0b1011, 2),
        (0xFF, 0x00, 8),
        (0x1FF, 0x000, 9),
        (2**64 - 1, 0, 64),
    ],
)
def test_hamming_distance_counts_exactly_the_changed_bits(
    first: int, second: int, distance: int
) -> None:
    assert hamming_distance(first, second) == distance
    assert hamming_distance(second, first) == distance


@pytest.mark.parametrize(
    ("pixels", "width", "height"),
    [(b"", 0, 0), (b"\x00" * 10, 4, 4), (b"\x00" * 16, -4, 4)],
)
def test_a_frame_that_is_not_width_times_height_grayscale_values_is_refused(
    pixels: bytes, width: int, height: int
) -> None:
    with pytest.raises(ValueError, match="grayscale"):
        hash_grayscale_frame(pixels, width, height)
