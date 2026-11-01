"""Resolve bundled, read-only runtime fixtures consistently in every install.

Operator-provided state and secrets must remain outside these packaged inputs.
"""

from pathlib import Path


def resource_root() -> Path:
    # Never search CWD or sibling site-packages directories: a registry beside
    # the wheel can be stale or controlled by another distribution. Checkout
    # authoring sources are separately compared byte-for-byte in tests.
    return Path(__file__).resolve().parent / "_data"
