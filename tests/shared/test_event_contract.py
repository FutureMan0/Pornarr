"""The event vocabulary the server publishes, against the one the contract names.

ADR 0020 L6 says "all twelve live events", and `docs/api-contract.md` L45-48
prints the twelve names in a block. Claim 13.20 of the gauntlet is that those
twelve are the event types, which is a claim about the whole product rather than
about one route, so it is asserted here by reading the publications out of the
source rather than by watching a stream long enough to see all of them.

`tests/e2e/admin-system.spec.ts` proves the transport - that a frame published in
the worker arrives on a stream opened against the API, with a Redis stream id
that `Last-Event-ID` resumes from. This file is about the vocabulary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

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
    """The names printed in the fenced block under `## Events`."""

    events = CONTRACT.read_text().split("## Events", 1)[1].split("```")[1]
    return re.findall(r"[a-z_]+\.[a-z_]+", events)


def test_the_contract_prints_exactly_twelve_names() -> None:
    """ADR 0020 L6: "all twelve live events"."""
    names = contract_event_types()
    assert len(names) == 12, names
    assert len(set(names)) == 12, names


def test_every_publication_site_is_found_by_the_reader_above() -> None:
    """Guard on the reader, so a shrinking result cannot look like agreement.

    Without this, deleting `publish` from `_PUBLISHERS` would make the test
    below pass by finding nothing.
    """
    published = published_event_types()
    for known in ("search.started", "import.completed", "download.progress", "scan.progress"):
        assert known in published, published
    assert len(published) >= 12, published


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The product publishes fourteen event types and only nine of them are in the "
        "contract's twelve. `download.completed` and `download.failed` are published by "
        "nothing, while `download.status`, `request.not_found`, `scan.progress`, "
        "`scan.completed`, `storage.low_space`, `settings.changed` and `notification` are "
        "published and undocumented. See .gauntlet/pieces/13-admin-system/HOLES.md, claim "
        "13.20, and .gauntlet/pieces/04-acquisition/BUILD.md defect 7 for the three of the "
        "five that acquisition closed: `request.created`, `download.queued` and "
        "`download.started`."
    ),
)
def test_the_published_event_types_are_the_twelve_the_contract_names() -> None:
    assert published_event_types() == set(contract_event_types())


def test_the_contract_names_five_frames_nothing_publishes() -> None:
    """The remaining half of the divergence that costs a reader a live update.

    `request.created`, `download.queued` and `download.started` are published
    now (`apps/api/pornarr_api/routers/requests.py`, at request creation and at
    grab). `download.completed` and `download.failed` are not: nothing marks a
    download job's own terminal outcome with a dedicated frame, only the
    generic `download.status`.
    """
    assert set(contract_event_types()) - published_event_types() == {
        "download.completed",
        "download.failed",
    }


def test_the_server_publishes_seven_frames_the_contract_does_not_name() -> None:
    """The other half: a client generated from the contract cannot know these exist."""
    assert published_event_types() - set(contract_event_types()) == {
        "download.status",
        "request.not_found",
        "scan.progress",
        "scan.completed",
        "storage.low_space",
        "settings.changed",
        "notification",
    }
