"""The event vocabulary the server publishes, against the one the contract names.

ADR 0020 L6 says "all nineteen live events", and `docs/api-contract.md` prints the
nineteen names in a block under `## Events`. Claim 13.20 of the gauntlet is that
those are the event types, which is a claim about the whole product rather than
about one route, so it is asserted here by reading the publications out of the
source rather than by watching a stream long enough to see all of them.

It said twelve on both counts until the seven frames the server published without
documenting them - `download.status`, `request.not_found`, `scan.progress`,
`scan.completed`, `storage.low_space`, `settings.changed` and `notification` -
were written down. A client is generated from the contract and could not know
they existed.

`tests/e2e/admin-system.spec.ts` proves the transport - that a frame published in
the worker arrives on a stream opened against the API, with a Redis stream id
that `Last-Event-ID` resumes from. This file is about the vocabulary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
CONTRACT = ROOT / "docs/api-contract.md"

# `publish_event` is the only publisher, but three modules wrap it in a local
# closure so a long function does not repeat the redis handle. Those wrappers
# take the event type in the same first position.
_PUBLISHERS = frozenset({"publish_event", "publish", "progress", "publish_progress"})
_SOURCE_TREES = ("apps", "packages")


def published_event_types() -> set[str]:
    """Every event type this repository can put on the stream."""

    found: set[str] = set()
    for tree in _SOURCE_TREES:
        for path in (ROOT / tree).rglob("*.py"):
            module = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(module):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else None
                name = node.func.id if isinstance(node.func, ast.Name) else name
                if name not in _PUBLISHERS or not node.args:
                    continue
                # `publish_event(redis, "type", ...)` versus the wrappers'
                # `publish("type", ...)`: take the first string literal, which
                # is the event type in both shapes.
                literal = next(
                    (
                        argument.value
                        for argument in node.args[:2]
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                    ),
                    None,
                )
                if literal is not None:
                    found.add(literal)
    return found


def contract_event_types() -> list[str]:
    """The names printed in the fenced block under `## Events`.

    The dotted half is optional because one frame has no dot in it:
    `notification` is a delivered notification rather than a step in the life of
    a search, a download or an import, and a reader that only matched `x.y`
    would have called the contract wrong for naming it.
    """

    events = CONTRACT.read_text().split("## Events", 1)[1].split("```")[1]
    return re.findall(r"[a-z_]+(?:\.[a-z_]+)?", events)


def test_the_contract_prints_exactly_nineteen_names() -> None:
    """ADR 0020 L6: "all nineteen live events"."""
    names = contract_event_types()
    assert len(names) == 19, names
    assert len(set(names)) == 19, names


def test_every_publication_site_is_found_by_the_reader_above() -> None:
    """Guard on the reader, so a shrinking result cannot look like agreement.

    Without this, deleting `publish` from `_PUBLISHERS` would make the test
    below pass by finding nothing.
    """
    published = published_event_types()
    for known in ("search.started", "import.completed", "download.progress", "scan.progress"):
        assert known in published, published
    assert len(published) >= 12, published


def test_the_published_event_types_are_the_ones_the_contract_names() -> None:
    assert published_event_types() == set(contract_event_types())


def test_every_frame_the_contract_names_is_published() -> None:
    """The half of the divergence that cost a reader a live update, closed.

    `request.created`, `download.queued` and `download.started` were closed by
    acquisition. `download.completed` and `download.failed` were the last two:
    nothing marked a download job's terminal outcome with the frame the
    contract names, only the generic `download.status`, so a client written
    against the contract watched for a download to end and was never told.
    `apps/worker/pornarr_worker/jobs/download_poll.py` publishes both now.
    """
    assert set(contract_event_types()) - published_event_types() == set()


def test_the_server_publishes_no_frame_the_contract_does_not_name() -> None:
    """The other half, closed by writing the seven undocumented frames down.

    A client is generated from the contract, so a frame the server publishes and
    the document does not name is one the client silently drops. Kept as its own
    assertion rather than folded into the equality above, because this is the
    direction that regresses: adding a `publish_event` call is a one-line change
    and editing `docs/api-contract.md` is a separate act of remembering.
    """
    assert published_event_types() - set(contract_event_types()) == set()
