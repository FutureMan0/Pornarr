"""Paging one list that is really several lists.

Browsing a shared library means reading from this instance and from every peer
at once, and the reader must not be able to tell where a page boundary fell.
Two things make that work and both live here rather than in the router:

- a **cursor**, because there is no single offset any more. Each source has its
  own position and the client must not be able to invent one, so the positions
  travel as one opaque string.
- a **k-way merge**, because each source is already sorted by the same key. If
  the merge picked wrong, the next page would either repeat an item or step over
  one, and the reader would never know which.

Deliberately free of HTTP and of SQLAlchemy: the merge is where the paging bugs
live, and a pure function is the only version of it that can be tested without a
second server.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# The source name the local library uses. Every other name is a peer id, which
# is a UUID and can therefore never collide with it.
LOCAL_SOURCE = "local"


@dataclass(frozen=True, slots=True)
class MergedPage[T]:
    """One page of a merge, and where each source has to resume."""

    items: tuple[tuple[str, T], ...]
    offsets: dict[str, int]
    exhausted: bool


def encode_cursor(sort: str, offsets: Mapping[str, int]) -> str:
    """Pack one offset per source into an opaque string."""
    payload = {"sort": sort, "offsets": {name: int(value) for name, value in offsets.items()}}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str, sort: str) -> dict[str, int]:
    """Unpack a cursor produced by :func:`encode_cursor`.

    The sort order is carried inside the cursor and checked here. A cursor taken
    from a "newest first" page and replayed against "by title" would describe
    positions in a list that no longer exists, which is a silently wrong page
    rather than an error the caller can see.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        stored_sort = payload["sort"]
        offsets = {str(name): int(value) for name, value in payload["offsets"].items()}
    except (AttributeError, KeyError, TypeError, ValueError, binascii.Error) as error:
        raise ValueError("Invalid library cursor.") from error
    # Checked outside the block above so it is not swallowed by it: this is the
    # one invalid cursor whose cause is worth naming.
    if stored_sort != sort:
        raise ValueError("The cursor belongs to a different sort order.")
    if any(value < 0 for value in offsets.values()):
        raise ValueError("Invalid library cursor.")
    return offsets


def merge_sources[T](
    pages: Mapping[str, Sequence[T]],
    offsets: Mapping[str, int],
    *,
    key: Callable[[T], tuple[Any, ...]],
    descending: bool,
    limit: int,
) -> MergedPage[T]:
    """Merge already-sorted pages into one, and say where to resume.

    Each source must be sorted by `key` in the same direction as `descending`,
    which is what the database and every peer already return. The resume offset
    of a source is how many of its items this page actually used, so a source
    whose items all lost the comparison is asked for the same items again rather
    than being skipped past them.
    """
    heads = dict.fromkeys(pages, 0)
    merged: list[tuple[str, T]] = []
    while len(merged) < limit:
        # The source name breaks a tie, so two instances holding the same title
        # produce the same order on every page rather than dict order.
        candidates = [
            (key(pages[name][heads[name]]), name)
            for name in sorted(pages)
            if heads[name] < len(pages[name])
        ]
        if not candidates:
            break
        _, chosen = (max if descending else min)(
            candidates, key=lambda candidate: (candidate[0], candidate[1])
        )
        merged.append((chosen, pages[chosen][heads[chosen]]))
        heads[chosen] += 1
    return MergedPage(
        items=tuple(merged),
        offsets={name: offsets.get(name, 0) + heads[name] for name in pages},
        exhausted=all(heads[name] >= len(items) for name, items in pages.items()),
    )
