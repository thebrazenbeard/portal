from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class BudgetEnvelope:
    lineage_id: str
    max_depth: int
    depth: int
    remaining_children: int
    remaining_active: int
    remaining_retries: int
    remaining_backend_jobs: int
    scope_id: str = "root"

    def __post_init__(self) -> None:
        numeric = {
            "max_depth": self.max_depth,
            "depth": self.depth,
            "remaining_children": self.remaining_children,
            "remaining_active": self.remaining_active,
            "remaining_retries": self.remaining_retries,
            "remaining_backend_jobs": self.remaining_backend_jobs,
        }
        if any(value < 0 for value in numeric.values()):
            raise ValueError("budget values must be non-negative")
        if self.depth > self.max_depth:
            raise ValueError("budget depth exceeds max depth")
        if not self.lineage_id.strip():
            raise ValueError("lineage id is required")
        if not self.scope_id.strip():
            raise ValueError("budget scope id is required")


def allocate_child_budget(
    parent: BudgetEnvelope,
    *,
    child_scope_id: str,
    child_children: int,
    child_active: int,
    child_retries: int,
    child_backend_jobs: int,
) -> tuple[BudgetEnvelope, BudgetEnvelope]:
    if parent.depth >= parent.max_depth:
        raise ValueError("depth exhausted")
    if not child_scope_id.strip():
        raise ValueError("child budget scope id is required")
    if child_scope_id == parent.scope_id:
        raise ValueError("child budget scope must differ from parent scope")

    requested = {
        "children": child_children,
        "active": child_active,
        "retries": child_retries,
        "backend jobs": child_backend_jobs,
    }
    if any(value < 0 for value in requested.values()):
        raise ValueError("child budget requests must be non-negative")

    available = {
        "children": parent.remaining_children,
        "active": parent.remaining_active,
        "retries": parent.remaining_retries,
        "backend jobs": parent.remaining_backend_jobs,
    }
    for name, value in requested.items():
        if value > available[name]:
            raise ValueError(f"{name} budget exceeded")

    parent_after = replace(
        parent,
        remaining_children=parent.remaining_children - child_children,
        remaining_active=parent.remaining_active - child_active,
        remaining_retries=parent.remaining_retries - child_retries,
        remaining_backend_jobs=parent.remaining_backend_jobs - child_backend_jobs,
    )
    child = BudgetEnvelope(
        lineage_id=parent.lineage_id,
        max_depth=parent.max_depth,
        depth=parent.depth + 1,
        remaining_children=child_children,
        remaining_active=child_active,
        remaining_retries=child_retries,
        remaining_backend_jobs=child_backend_jobs,
        scope_id=child_scope_id,
    )
    return parent_after, child
