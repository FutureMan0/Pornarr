"""Perceptual-hash primitives for non-destructive duplicate detection."""

from __future__ import annotations

from itertools import pairwise

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
