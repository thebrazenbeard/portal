from __future__ import annotations

from collections.abc import Collection


def narrow_capabilities(
    parent_capabilities: Collection[str],
    target_capabilities: Collection[str],
) -> tuple[str, ...]:
    """Return the deterministic child capability ceiling.

    Delegation may only preserve capabilities held by the parent and
    permitted by the target. It cannot synthesize new capability.
    """
    return tuple(sorted(set(parent_capabilities) & set(target_capabilities)))
