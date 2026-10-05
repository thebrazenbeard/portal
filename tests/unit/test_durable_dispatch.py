from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.models import ExactSubject
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _work(commit: str = "a" * 40) -> WorkUnit:
    return WorkUnit(
        id="dispatch-root",
        root_frontier_id="frontier-dispatch",
        parent_work_id=None,
        inputs=(
            ExactSubject(
                repository="thebrazenbeard/project-runner",
                ref="work/public-safe-portfolio-registry-v2",
                commit=commit,
                path="runner/durable_dispatch.py",
            ),
        ),
        operation="INSPECT",
        required_capabilities=("read", "analyze"),
        collision_keys=("project:project-runner",),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 1,
            "backend_jobs": 1,
        },
        expected_outputs=("backend-result",),
        completion_criteria=("verified",),
        status=WorkUnitStatus.PENDING,
        payload={"mode": "durable-dispatch-test"},
    )


def _budget(
    *,
    active: int = 1,
    retries: int = 1,
    backend_jobs: int = 1,
) -> BudgetEnvelope:
    return BudgetEnvelope(
        lineage_id="dispatch-lineage",
        max_depth=2,
        depth=0,
        remaining_children=0,
        remaining_active=active,
        remaining_retries=retries,
        remaining_backend_jobs=backend_jobs,
    )


def _persist_root(db: Path, *, budget: BudgetEnvelope | None = None):
    work = _work()
    fingerprint = work_unit_fingerprint(work)
    durable_budget = budget or _budget()

    budgets = SqliteBudgetStore(db)
    assert budgets.put_initial(durable_budget) == 1
    budgets.close()

    works = SqliteRecursiveWorkStore(db)
    works.put_initial(
        work=work,
        lineage_id=durable_budget.lineage_id,
        budget_scope_id=durable_budget.scope_id,
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    works.close()
    return work, fingerprint, durable_budget


def test_dispatch_admission_commits_budget_lease_and_claimed_state_together(
    tmp_path: Path,
):
    db = tmp_path / "dispatch.db"
    work, fingerprint, budget = _persist_root(db)

    store = SqliteDispatchAdmissionStore(db)
    admitted = store.admit(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        budget_scope_id=budget.scope_id,
        expected_budget_generation=1,
        expected_work_generation=1,
        holder="worker-a",
        now=0.0,
        ttl=30.0,
    )
    store.close()

    assert admitted.work.status is WorkUnitStatus.CLAIMED
    assert work_unit_fingerprint(admitted.work) == fingerprint
    assert admitted.budget_after.remaining_active == 0
    assert admitted.budget_after.remaining_backend_jobs == 0
    assert admitted.budget_generation == 2
    assert admitted.work_generation == 2
    assert admitted.retry_consumed == 0
    assert admitted.lease.fencing_token == 1

    budgets = SqliteBudgetStore(db)
    reopened_budget, budget_generation = budgets.get(
        budget.lineage_id,
        budget.scope_id,
    )
    assert reopened_budget == admitted.budget_after
    assert budget_generation == 2
    budgets.close()

    works = SqliteRecursiveWorkStore(db)
    reopened_work = works.get(budget.lineage_id, fingerprint)
    assert reopened_work is not None
    assert reopened_work.work.status is WorkUnitStatus.CLAIMED
    assert reopened_work.generation == 2
    works.close()

    leases = SqliteLeaseStore(db)
    row = leases.connection.execute(
        """
        SELECT holder, fencing_token, completed
        FROM leases
        WHERE work_fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    assert row == ("worker-a", 1, 0)
    leases.close()


def test_stale_budget_generation_rolls_back_dispatch_admission(tmp_path: Path):
    db = tmp_path / "dispatch.db"
    _, fingerprint, budget = _persist_root(db)

    store = SqliteDispatchAdmissionStore(db)
    with pytest.raises(ValueError, match="budget generation"):
        store.admit(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=budget.scope_id,
            expected_budget_generation=0,
            expected_work_generation=1,
            holder="worker-a",
            now=0.0,
            ttl=30.0,
        )
    store.close()

    budgets = SqliteBudgetStore(db)
    reopened_budget, generation = budgets.get(budget.lineage_id, budget.scope_id)
    assert reopened_budget == budget
    assert generation == 1
    budgets.close()

    works = SqliteRecursiveWorkStore(db)
    reopened_work = works.get(budget.lineage_id, fingerprint)
    assert reopened_work is not None
    assert reopened_work.work.status is WorkUnitStatus.PENDING
    assert reopened_work.generation == 1
    works.close()

    leases = SqliteLeaseStore(db)
    assert (
        leases.connection.execute(
            "SELECT 1 FROM leases WHERE work_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        is None
    )
    leases.close()


def test_active_lease_collision_does_not_consume_budget_or_claim_work(tmp_path: Path):
    db = tmp_path / "dispatch.db"
    _, fingerprint, budget = _persist_root(db)

    leases = SqliteLeaseStore(db)
    existing = leases.claim(fingerprint, holder="worker-a", now=0.0, ttl=30.0)
    assert existing is not None
    leases.close()

    store = SqliteDispatchAdmissionStore(db)
    with pytest.raises(ValueError, match="active lease"):
        store.admit(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=budget.scope_id,
            expected_budget_generation=1,
            expected_work_generation=1,
            holder="worker-b",
            now=1.0,
            ttl=30.0,
        )
    store.close()

    budgets = SqliteBudgetStore(db)
    reopened_budget, generation = budgets.get(budget.lineage_id, budget.scope_id)
    assert reopened_budget == budget
    assert generation == 1
    budgets.close()

    works = SqliteRecursiveWorkStore(db)
    reopened_work = works.get(budget.lineage_id, fingerprint)
    assert reopened_work is not None
    assert reopened_work.work.status is WorkUnitStatus.PENDING
    assert reopened_work.generation == 1
    works.close()


@pytest.mark.parametrize(
    "retry_status",
    (
        WorkUnitStatus.FAILED_RETRYABLE,
        WorkUnitStatus.OUTCOME_UNKNOWN,
    ),
)
def test_redispatch_consumes_retry_budget_and_reclaims_expired_fence(
    tmp_path: Path,
    retry_status: WorkUnitStatus,
):
    db = tmp_path / f"{retry_status.value.lower()}.db"
    budget = _budget(active=2, retries=1, backend_jobs=2)
    _, fingerprint, _ = _persist_root(db, budget=budget)

    leases = SqliteLeaseStore(db)
    first = leases.claim(fingerprint, holder="worker-a", now=0.0, ttl=10.0)
    assert first is not None

    works = SqliteRecursiveWorkStore(db)
    running = works.compare_and_swap_status(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=first,
        now=1.0,
    )
    assert running.generation == 2
    retryable = works.compare_and_swap_status(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=retry_status,
        lease=first,
        now=2.0,
    )
    assert retryable.generation == 3
    works.close()
    leases.close()

    store = SqliteDispatchAdmissionStore(db)
    admitted = store.admit(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        budget_scope_id=budget.scope_id,
        expected_budget_generation=1,
        expected_work_generation=3,
        holder="worker-b",
        now=11.0,
        ttl=30.0,
    )
    store.close()

    assert admitted.retry_consumed == 1
    assert admitted.budget_after.remaining_active == 1
    assert admitted.budget_after.remaining_backend_jobs == 1
    assert admitted.budget_after.remaining_retries == 0
    assert admitted.budget_generation == 2
    assert admitted.work.status is WorkUnitStatus.CLAIMED
    assert admitted.work_generation == 4
    assert admitted.lease.fencing_token == 2
