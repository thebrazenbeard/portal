from __future__ import annotations

import json
from pathlib import Path
import tempfile

from runner.budgets import BudgetEnvelope
from runner.decompose import admit_child_work
from runner.models import ExactSubject
from runner.persistent_state import SqliteBudgetStore
from runner.recursive_admission import SqliteRecursiveAdmissionStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


LINEAGE = "m6-two-level-recursive-restart-proof"


def _work(
    work_id: str,
    *,
    depth: int,
    parent_work_id: str | None,
    commit: str,
    operation: str,
) -> WorkUnit:
    return WorkUnit(
        id=work_id,
        root_frontier_id="m6-two-level-recursive-restart-proof",
        parent_work_id=parent_work_id,
        inputs=(
            ExactSubject(
                repository="thebrazenbeard/project-runner",
                ref="work/public-safe-portfolio-registry-v2",
                commit=commit,
                path="runner/recursive_admission.py",
            ),
        ),
        operation=operation,
        required_capabilities=("read", "analyze"),
        collision_keys=("project:project-runner",),
        recursion_depth=depth,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        expected_outputs=("durable-recursive-state",),
        completion_criteria=("restart-readback",),
        status=WorkUnitStatus.PENDING,
        payload={"proof": "m6-two-level-recursive-restart"},
    )


def _root_budget() -> BudgetEnvelope:
    return BudgetEnvelope(
        lineage_id=LINEAGE,
        max_depth=3,
        depth=0,
        remaining_children=2,
        remaining_active=2,
        remaining_retries=0,
        remaining_backend_jobs=2,
    )


def main() -> int:
    root = _work(
        "root",
        depth=0,
        parent_work_id=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    root_fp = work_unit_fingerprint(root)
    root_budget = _root_budget()

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "m6-recursive-proof.sqlite3"

        budget_store = SqliteBudgetStore(db)
        root_budget_generation = budget_store.put_initial(root_budget)
        budget_store.close()

        work_store = SqliteRecursiveWorkStore(db)
        work_store.put_initial(
            work=root,
            lineage_id=LINEAGE,
            budget_scope_id="root",
            parent_fingerprint=None,
            ancestry_fingerprints={root_fp},
            effective_capabilities={"read", "analyze"},
        )
        work_store.close()

        child = _work(
            "child",
            depth=1,
            parent_work_id=root.id,
            commit="b" * 40,
            operation="REREVIEW",
        )
        child_admission = admit_child_work(
            parent=root,
            child=child,
            parent_budget=root_budget,
            parent_capabilities={"read", "analyze"},
            target_capabilities={"read", "analyze"},
            ancestry_fingerprints={root_fp},
            child_children=1,
            child_active=1,
            child_retries=0,
            child_backend_jobs=1,
        )
        child_fp = work_unit_fingerprint(child_admission.work)

        admission_store = SqliteRecursiveAdmissionStore(db)
        child_commit = admission_store.commit_child(
            parent_work_fingerprint=root_fp,
            parent_budget_before=root_budget,
            expected_parent_budget_generation=root_budget_generation,
            target_capabilities={"read", "analyze"},
            admission=child_admission,
        )
        admission_store.close()

        # Simulate a process boundary before the child itself decomposes.
        budget_store = SqliteBudgetStore(db)
        durable_child_budget, child_budget_generation = budget_store.get(
            LINEAGE,
            child_admission.child_budget.scope_id,
        )
        budget_store.close()

        work_store = SqliteRecursiveWorkStore(db)
        durable_child = work_store.get(LINEAGE, child_fp)
        if durable_child is None:
            raise RuntimeError("child work did not survive first restart")
        work_store.close()

        grandchild = _work(
            "grandchild",
            depth=2,
            parent_work_id=durable_child.work.id,
            commit="c" * 40,
            operation="RETEST",
        )
        grandchild_admission = admit_child_work(
            parent=durable_child.work,
            child=grandchild,
            parent_budget=durable_child_budget,
            parent_capabilities=set(durable_child.effective_capabilities),
            target_capabilities={"read", "analyze"},
            ancestry_fingerprints=durable_child.ancestry_fingerprints,
            child_children=0,
            child_active=1,
            child_retries=0,
            child_backend_jobs=1,
        )
        grandchild_fp = work_unit_fingerprint(grandchild_admission.work)

        admission_store = SqliteRecursiveAdmissionStore(db)
        grandchild_commit = admission_store.commit_child(
            parent_work_fingerprint=child_fp,
            parent_budget_before=durable_child_budget,
            expected_parent_budget_generation=child_budget_generation,
            target_capabilities={"read", "analyze"},
            admission=grandchild_admission,
        )
        admission_store.close()

        # Simulate a second process boundary and independently reconstruct the lineage.
        budget_store = SqliteBudgetStore(db)
        final_root_budget, final_root_generation = budget_store.get(LINEAGE, "root")
        final_child_budget, final_child_generation = budget_store.get(
            LINEAGE,
            child_admission.child_budget.scope_id,
        )
        final_grandchild_budget, final_grandchild_generation = budget_store.get(
            LINEAGE,
            grandchild_admission.child_budget.scope_id,
        )
        budget_store.close()

        work_store = SqliteRecursiveWorkStore(db)
        final_root = work_store.get(LINEAGE, root_fp)
        final_child = work_store.get(LINEAGE, child_fp)
        final_grandchild = work_store.get(LINEAGE, grandchild_fp)
        work_store.close()

        if final_root is None or final_child is None or final_grandchild is None:
            raise RuntimeError("recursive work lineage is incomplete after second restart")

        if final_root.ancestry_fingerprints != frozenset({root_fp}):
            raise RuntimeError("root ancestry changed across restart")
        if final_child.ancestry_fingerprints != frozenset({root_fp, child_fp}):
            raise RuntimeError("child ancestry changed across restart")
        if final_grandchild.ancestry_fingerprints != frozenset(
            {root_fp, child_fp, grandchild_fp}
        ):
            raise RuntimeError("grandchild ancestry changed across restart")
        if final_child.parent_fingerprint != root_fp:
            raise RuntimeError("child parent identity changed across restart")
        if final_grandchild.parent_fingerprint != child_fp:
            raise RuntimeError("grandchild parent identity changed across restart")

        if final_root_generation != child_commit.parent_budget_generation:
            raise RuntimeError("root budget generation mismatch after child admission")
        if final_child_generation != grandchild_commit.parent_budget_generation:
            raise RuntimeError("child budget generation mismatch after grandchild admission")
        if final_grandchild_generation != 1:
            raise RuntimeError("grandchild budget generation must start at one")

        if final_root_budget != child_admission.parent_budget:
            raise RuntimeError("root budget transfer did not survive restart")
        if final_child_budget != grandchild_admission.parent_budget:
            raise RuntimeError("child budget transfer did not survive restart")
        if final_grandchild_budget != grandchild_admission.child_budget:
            raise RuntimeError("grandchild budget did not survive restart")

        if final_root.work.recursion_depth != 0:
            raise RuntimeError("root depth changed")
        if final_child.work.recursion_depth != 1:
            raise RuntimeError("child depth changed")
        if final_grandchild.work.recursion_depth != 2:
            raise RuntimeError("grandchild depth changed")

        expected_capabilities = ("analyze", "read")
        if final_root.effective_capabilities != expected_capabilities:
            raise RuntimeError("root capability ceiling changed across restart")
        if final_child.effective_capabilities != expected_capabilities:
            raise RuntimeError("child capability ceiling changed across restart")
        if final_grandchild.effective_capabilities != expected_capabilities:
            raise RuntimeError("grandchild capability ceiling changed across restart")

    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "lineage": LINEAGE,
                "levels": 3,
                "restart_boundaries": 2,
                "root_budget_generation": final_root_generation,
                "child_budget_generation": final_child_generation,
                "grandchild_budget_generation": final_grandchild_generation,
                "root_ancestry_size": len(final_root.ancestry_fingerprints),
                "child_ancestry_size": len(final_child.ancestry_fingerprints),
                "grandchild_ancestry_size": len(final_grandchild.ancestry_fingerprints),
                "root_capability_ceiling": list(final_root.effective_capabilities),
                "child_capability_ceiling": list(final_child.effective_capabilities),
                "grandchild_capability_ceiling": list(final_grandchild.effective_capabilities),
                "atomic_child_commits": 2,
                "max_depth": final_grandchild_budget.max_depth,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
