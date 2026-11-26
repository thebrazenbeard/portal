from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .currentness import observation_locus, subject_changed
from .graph import affected_edges
from .models import DependencyEdge, DependencyReaction, ExactSubject, Observation


@dataclass(frozen=True)
class Invalidation:
    dependency_id: str
    provider: str
    consumer: str
    changed_subject: ExactSubject
    reaction: DependencyReaction


def derive_invalidations(
    previous_observations: Iterable[Observation],
    current_observations: Iterable[Observation],
    edges: Iterable[DependencyEdge],
) -> tuple[Invalidation, ...]:
    previous_by_key = {observation_locus(item): item for item in previous_observations}
    edge_tuple = tuple(edges)
    result: list[Invalidation] = []

    for current in current_observations:
        previous = previous_by_key.get(observation_locus(current))
        if not subject_changed(previous, current):
            continue
        for edge in affected_edges(edge_tuple, current.subject):
            if edge.provider != current.target:
                continue
            result.append(
                Invalidation(
                    dependency_id=edge.id,
                    provider=edge.provider,
                    consumer=edge.consumer,
                    changed_subject=current.subject,
                    reaction=edge.reaction,
                )
            )

    return tuple(result)
