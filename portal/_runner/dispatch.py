from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

from .backends import BackendResult, ExecutionBackend
from .budgets import BudgetEnvelope
from .collisions import partition_collision_groups
from .dedup import deduplicate_frontiers, frontier_fingerprint
from .leases import Lease, LeaseStore
from .models import Frontier, FrontierStatus
from .prioritize import rank_frontiers
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


@dataclass(frozen=True)
class DispatchAttempt:
    frontier: Frontier
    work: WorkUnit
    lease: Lease
    result: BackendResult


@dataclass(frozen=True)
class DispatchBatch:
    attempts: tuple[DispatchAttempt, ...]
    remaining_budget: BudgetEnvelope


def frontier_to_work_unit(frontier: Frontier, *, depth: int = 0) -> WorkUnit:
    fingerprint = frontier_fingerprint(frontier)
    return WorkUnit(
        id=f"work-{fingerprint[:16]}",
        root_frontier_id=frontier.id,
        parent_work_id=None,
        inputs=(frontier.subject,),
        operation=frontier.work_type,
        required_capabilities=frontier.required_capabilities,
        collision_keys=frontier.collision_keys,
        recursion_depth=depth,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        expected_outputs=("backend-result",),
        completion_criteria=(
            "exact-subject-current",
            "completion-evidence-verified",
        ),
        status=WorkUnitStatus.PENDING,
    )


def dispatch_ready(
    frontiers: Iterable[Frontier],
    *,
    lease_store: LeaseStore,
    backend: ExecutionBackend,
    budget: BudgetEnvelope,
    holder: str,
    now: float,
    lease_ttl: float,
    work_factory: Callable[[Frontier, int], WorkUnit] | None = None,
) -> DispatchBatch:
    deduped = deduplicate_frontiers(frontiers)
    ranked = rank_frontiers(deduped)
    ready = tuple(
        decision.frontier
        for decision in ranked
        if decision.frontier.status is FrontierStatus.READY
    )

    groups = partition_collision_groups(ready)
    candidates = tuple(group[0] for group in groups if group)
    remaining = budget
    attempts: list[DispatchAttempt] = []

    for frontier in candidates:
        if remaining.remaining_active <= 0 or remaining.remaining_backend_jobs <= 0:
            break

        work = (
            frontier_to_work_unit(frontier, depth=remaining.depth)
            if work_factory is None
            else work_factory(frontier, remaining.depth)
        )
        fingerprint = work_unit_fingerprint(work)
        lease = lease_store.claim(
            fingerprint,
            holder=holder,
            now=now,
            ttl=lease_ttl,
        )
        if lease is None:
            continue

        result = backend.execute(work)
        attempts.append(
            DispatchAttempt(
                frontier=frontier,
                work=work,
                lease=lease,
                result=result,
            )
        )
        remaining = replace(
            remaining,
            remaining_active=remaining.remaining_active - 1,
            remaining_backend_jobs=remaining.remaining_backend_jobs - 1,
        )

    return DispatchBatch(attempts=tuple(attempts), remaining_budget=remaining)
