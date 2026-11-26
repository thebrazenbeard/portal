from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace

from .budgets import BudgetEnvelope, allocate_child_budget
from .capabilities import narrow_capabilities
from .work_units import WorkUnit, work_unit_fingerprint


@dataclass(frozen=True)
class ChildAdmission:
    work: WorkUnit
    parent_budget: BudgetEnvelope
    child_budget: BudgetEnvelope
    effective_capabilities: tuple[str, ...]
    ancestry_fingerprints: frozenset[str]


def admit_child_work(
    *,
    parent: WorkUnit,
    child: WorkUnit,
    parent_budget: BudgetEnvelope,
    parent_capabilities: Collection[str],
    target_capabilities: Collection[str],
    ancestry_fingerprints: Collection[str],
    child_children: int,
    child_active: int,
    child_retries: int,
    child_backend_jobs: int,
) -> ChildAdmission:
    if parent_budget.depth != parent.recursion_depth:
        raise ValueError("parent budget depth does not match parent work depth")
    if child.recursion_depth != parent.recursion_depth + 1:
        raise ValueError("child depth must be exactly parent depth + 1")

    parent_fingerprint = work_unit_fingerprint(parent)
    child_fingerprint = work_unit_fingerprint(child)
    ancestry = set(ancestry_fingerprints)
    ancestry.add(parent_fingerprint)

    if child_fingerprint in ancestry:
        raise ValueError("recursive decomposition cycle detected")

    effective = narrow_capabilities(parent_capabilities, target_capabilities)
    if not set(child.required_capabilities).issubset(set(effective)):
        raise ValueError("child capability requirement exceeds inherited ceiling")

    parent_after, child_budget = allocate_child_budget(
        parent_budget,
        child_scope_id=f"work:{child_fingerprint}",
        child_children=child_children,
        child_active=child_active,
        child_retries=child_retries,
        child_backend_jobs=child_backend_jobs,
    )
    if child_budget.depth != child.recursion_depth:
        raise ValueError("child work depth does not match inherited budget depth")

    admitted = replace(
        child,
        budget_allocation={
            "children": child_budget.remaining_children,
            "active": child_budget.remaining_active,
            "retries": child_budget.remaining_retries,
            "backend_jobs": child_budget.remaining_backend_jobs,
        },
    )
    ancestry.add(child_fingerprint)

    return ChildAdmission(
        work=admitted,
        parent_budget=parent_after,
        child_budget=child_budget,
        effective_capabilities=effective,
        ancestry_fingerprints=frozenset(ancestry),
    )
