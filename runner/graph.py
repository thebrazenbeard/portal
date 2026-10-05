from __future__ import annotations

from collections.abc import Iterable

from .models import DependencyEdge, DependencySelector, ExactSubject


def selector_matches(selector: DependencySelector, subject: ExactSubject) -> bool:
    if selector.repository != subject.repository:
        return False
    if selector.ref is not None and selector.ref != subject.ref:
        return False
    if selector.path_prefix is not None:
        if subject.path is None:
            return False
        if not subject.path.startswith(selector.path_prefix):
            return False
    return True


def affected_edges(
    edges: Iterable[DependencyEdge],
    changed_subject: ExactSubject,
) -> tuple[DependencyEdge, ...]:
    return tuple(edge for edge in edges if selector_matches(edge.selector, changed_subject))
