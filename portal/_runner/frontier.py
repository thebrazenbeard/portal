from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
import hashlib
import json

from .models import CostClass, DependencyReaction, Frontier, FrontierStatus
from .propagate import Invalidation


_REQUIRED_CAPABILITIES: dict[DependencyReaction, tuple[str, ...]] = {
    DependencyReaction.INSPECT: ("analyze",),
    DependencyReaction.RETEST: ("analyze",),
    DependencyReaction.REREVIEW: ("analyze",),
    DependencyReaction.REQUALIFY: ("analyze",),
    DependencyReaction.BLOCK: (),
    DependencyReaction.NO_ACTION: (),
}


def _frontier_id(invalidation: Invalidation) -> str:
    payload = {
        "dependency_id": invalidation.dependency_id,
        "consumer": invalidation.consumer,
        "reaction": invalidation.reaction.value,
        "subject": invalidation.changed_subject.identity(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"frontier-{digest[:16]}"


def _collision_keys(invalidation: Invalidation) -> tuple[str, ...]:
    # Collision keys describe the mutable target, not shared read-only evidence.
    # Different consumers of the same provider artifact therefore remain parallelizable.
    return (f"project:{invalidation.consumer}",)


def derive_frontiers(
    invalidations: Iterable[Invalidation],
    capability_lookup: Mapping[str, Collection[str]],
    *,
    scheduling_lookup: Mapping[str, bool] | None = None,
) -> tuple[Frontier, ...]:
    result: list[Frontier] = []
    for invalidation in invalidations:
        if invalidation.reaction is DependencyReaction.NO_ACTION:
            continue

        required = _REQUIRED_CAPABILITIES[invalidation.reaction]
        available = set(capability_lookup.get(invalidation.consumer, ()))
        scheduling_known = (
            scheduling_lookup is not None
            and invalidation.consumer in scheduling_lookup
        )
        schedulable = (
            bool(scheduling_lookup[invalidation.consumer])
            if scheduling_known
            else True
        )
        if invalidation.reaction is DependencyReaction.BLOCK:
            status = FrontierStatus.WAITING_DEPENDENCY
        elif not schedulable:
            status = FrontierStatus.WAITING_SCHEDULING
        elif not set(required).issubset(available):
            status = FrontierStatus.WAITING_AUTHORITY
        else:
            status = FrontierStatus.READY

        result.append(
            Frontier(
                id=_frontier_id(invalidation),
                project=invalidation.consumer,
                subject=invalidation.changed_subject,
                work_type=invalidation.reaction.value,
                reason=(
                    f"dependency {invalidation.dependency_id} requires "
                    f"{invalidation.reaction.value} after {invalidation.provider} changed"
                ),
                dependencies=(invalidation.dependency_id,),
                required_capabilities=required,
                collision_keys=_collision_keys(invalidation),
                cost_class=CostClass.SMALL,
                priority_inputs={
                    "fanout": 1,
                    "blocked_downstream": 0,
                    "staleness_risk": 1,
                    "failure_severity": 0,
                    "declared_priority": 0,
                    "cost": 1,
                    "authority_available": int(status is not FrontierStatus.WAITING_AUTHORITY),
                    "scheduling_eligible": int(status is not FrontierStatus.WAITING_SCHEDULING),
                    "executable_now": int(status is FrontierStatus.READY),
                },
                status=status,
            )
        )
    return tuple(result)
