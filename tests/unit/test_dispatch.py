from runner.backends import MockBackend
from runner.budgets import BudgetEnvelope
from runner.dispatch import dispatch_ready
from runner.leases import InMemoryLeaseStore
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus


def _frontier(
    id,
    *,
    project="vera",
    commit="a" * 40,
    work_type="REREVIEW",
    status=FrontierStatus.READY,
    collision_keys=None,
    priority=0,
):
    return Frontier(
        id=id,
        project=project,
        subject=ExactSubject(
            repository=f"thebrazenbeard/{project}",
            ref="main",
            commit=commit,
        ),
        work_type=work_type,
        reason="test",
        dependencies=("dep-1",),
        required_capabilities=("analyze",),
        collision_keys=tuple(collision_keys or (f"project:{project}",)),
        cost_class=CostClass.SMALL,
        priority_inputs={
            "fanout": 1,
            "blocked_downstream": 0,
            "staleness_risk": 1,
            "failure_severity": 0,
            "declared_priority": priority,
            "cost": 1,
            "authority_available": int(status is not FrontierStatus.WAITING_AUTHORITY),
            "executable_now": int(status is FrontierStatus.READY),
        },
        status=status,
    )


def _budget(active=10, jobs=10):
    return BudgetEnvelope(
        lineage_id="run-1",
        max_depth=3,
        depth=0,
        remaining_children=10,
        remaining_active=active,
        remaining_retries=3,
        remaining_backend_jobs=jobs,
    )


def test_blocked_frontier_never_dispatches():
    backend = MockBackend()
    result = dispatch_ready(
        (_frontier("blocked", status=FrontierStatus.WAITING_AUTHORITY),),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    assert result.attempts == ()
    assert backend.executed == []


def test_unrelated_ready_frontiers_are_both_admitted():
    backend = MockBackend()
    result = dispatch_ready(
        (
            _frontier("a", project="vera"),
            _frontier("b", project="hc-brain", commit="b" * 40),
        ),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    assert len(result.attempts) == 2
    assert len(backend.executed) == 2


def test_colliding_ready_frontiers_admit_only_one_active_representative():
    backend = MockBackend()
    result = dispatch_ready(
        (
            _frontier("a", work_type="REREVIEW", collision_keys=("shared-target",), priority=2),
            _frontier("b", work_type="RETEST", commit="b" * 40, collision_keys=("shared-target",), priority=1),
        ),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    assert len(result.attempts) == 1
    assert len(backend.executed) == 1


def test_duplicate_semantic_frontier_claims_and_executes_once():
    backend = MockBackend()
    store = InMemoryLeaseStore()
    result = dispatch_ready(
        (_frontier("a"), _frontier("different-id")),
        lease_store=store,
        backend=backend,
        budget=_budget(),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    assert len(result.attempts) == 1
    assert len(backend.executed) == 1


def test_dispatch_respects_backend_job_and_active_budget():
    backend = MockBackend()
    result = dispatch_ready(
        (
            _frontier("a", project="vera"),
            _frontier("b", project="hc-brain", commit="b" * 40),
        ),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(active=1, jobs=1),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    assert len(result.attempts) == 1
    assert result.remaining_budget.remaining_active == 0
    assert result.remaining_budget.remaining_backend_jobs == 0
