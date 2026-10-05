from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from .models import Frontier


def partition_collision_groups(frontiers: Iterable[Frontier]) -> tuple[tuple[Frontier, ...], ...]:
    items = tuple(frontiers)
    if not items:
        return ()

    key_to_indexes: dict[str, list[int]] = defaultdict(list)
    for index, frontier in enumerate(items):
        for key in set(frontier.collision_keys):
            key_to_indexes[key].append(index)

    adjacency: list[set[int]] = [set() for _ in items]
    for indexes in key_to_indexes.values():
        for index in indexes:
            adjacency[index].update(other for other in indexes if other != index)

    seen: set[int] = set()
    groups: list[tuple[Frontier, ...]] = []
    for start in range(len(items)):
        if start in seen:
            continue
        queue = deque([start])
        component: list[int] = []
        seen.add(start)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        groups.append(tuple(items[index] for index in sorted(component)))

    return tuple(groups)


def independent_frontiers(frontiers: Iterable[Frontier]) -> tuple[Frontier, ...]:
    return tuple(group[0] for group in partition_collision_groups(frontiers) if group)
