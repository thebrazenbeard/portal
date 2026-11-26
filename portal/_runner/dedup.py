from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Iterable

from .models import Frontier, FrontierStatus


_BLOCKING_PRECEDENCE = {
    FrontierStatus.READY: 0,
    FrontierStatus.RUNNING: 1,
    FrontierStatus.VERIFYING: 1,
    FrontierStatus.COMPLETE: 1,
    FrontierStatus.SUPERSEDED: 1,
    FrontierStatus.FAILED_RETRYABLE: 2,
    FrontierStatus.FAILED_DETERMINISTIC: 3,
    FrontierStatus.WAITING_DEPENDENCY: 4,
    FrontierStatus.WAITING_AUTHORITY: 5,
    FrontierStatus.WAITING_SCHEDULING: 6,
    FrontierStatus.OUTCOME_UNKNOWN: 7,
}


def frontier_fingerprint(frontier: Frontier) -> str:
    # Provenance paths explain why the work exists; they do not define the work itself.
    # This lets the same operation discovered through multiple dependency edges deduplicate.
    payload = {
        "project": frontier.project,
        "subject": {
            "repository": frontier.subject.repository,
            "ref": frontier.subject.ref,
            "commit": frontier.subject.commit,
            "path": frontier.subject.path,
            "digest": frontier.subject.digest,
        },
        "work_type": frontier.work_type,
        "required_capabilities": sorted(frontier.required_capabilities),
        "collision_keys": sorted(frontier.collision_keys),
        "cost_class": frontier.cost_class.value,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _merged_priority_inputs(a: Frontier, b: Frontier) -> dict[str, int]:
    keys = set(a.priority_inputs) | set(b.priority_inputs)
    return {
        key: max(int(a.priority_inputs.get(key, 0)), int(b.priority_inputs.get(key, 0)))
        for key in keys
    }


def deduplicate_frontiers(frontiers: Iterable[Frontier]) -> tuple[Frontier, ...]:
    by_fingerprint: dict[str, Frontier] = {}
    order: list[str] = []
    for frontier in frontiers:
        fingerprint = frontier_fingerprint(frontier)
        existing = by_fingerprint.get(fingerprint)
        if existing is None:
            by_fingerprint[fingerprint] = frontier
            order.append(fingerprint)
            continue

        merged_status = (
            frontier.status
            if _BLOCKING_PRECEDENCE[frontier.status] > _BLOCKING_PRECEDENCE[existing.status]
            else existing.status
        )
        by_fingerprint[fingerprint] = replace(
            existing,
            dependencies=tuple(sorted(set(existing.dependencies) | set(frontier.dependencies))),
            priority_inputs=_merged_priority_inputs(existing, frontier),
            status=merged_status,
        )
    return tuple(by_fingerprint[item] for item in order)
