"""Every workspace member must be importable.

This is the acceptance criterion from #12 expressed as a test rather than as a
manual step, so a package that stops resolving fails CI instead of being noticed
later by whoever happens to import it.
"""

import importlib

import pytest

WORKSPACE_PACKAGES = [
    "pornarr_shared",
    "pornarr_core",
    "pornarr_db",
    "pornarr_integrations",
    "pornarr_media",
    "pornarr_api",
    "pornarr_worker",
]


@pytest.mark.parametrize("name", WORKSPACE_PACKAGES)
def test_package_is_importable(name: str) -> None:
    module = importlib.import_module(name)
    assert module.PACKAGE_ROLE


def test_core_has_no_io_dependencies() -> None:
    """packages/core must not depend on anything that performs I/O.

    The no-I/O rule is the reason core is testable without fixtures. It is easy
    to break by adding one convenient import, so it is asserted rather than
    documented.
    """
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).parent.parent / "packages" / "core" / "pyproject.toml"
    declared = tomllib.loads(manifest.read_text())["project"]["dependencies"]
    assert declared == [], f"packages/core must declare no dependencies, found {declared}"
