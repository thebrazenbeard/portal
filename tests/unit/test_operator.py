from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.m6_github import frontier_to_github_inspection_work
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus
from runner.operator import (
    build_github_read_backend,
    run_durable_github_read_inspection,
    summarize_recovery_state,
)
from runner.persistent_state import SqliteBudgetStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnitStatus


PROVIDER = ExactSubject(
    repository="example/provider",
    ref="main",
    commit="a" * 40,
)
TARGET = ExactSubject(
    repository="example/consumer",
    ref="main",
    commit="b" * 40,
)


class FakeTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.calls = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.calls.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository: str, path: str, ref: str):
        raise AssertionError("read_file is outside the read-inspection route")

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("mutation is outside the read-inspection route")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str:
        raise AssertionError("mutation is outside the read-inspection route")


def _frontier() -> Frontier:
    return Frontier(
        id="frontier-operator-test",
        project="consumer",
        subject=PROVIDER,
        work_type="INSPECT",
        reason="provider changed",
        dependencies=("dep-1",),
        required_capabilities=("analyze",),
        collision_keys=("project:consumer",),
        cost_class=CostClass.SMALL,
        priority_inputs={"urgency": 1},
        status=FrontierStatus.READY,
    )


def _clock():
    return 1.0


def test_durable_operator_executes_real_read_route_and_finalizes(tmp_path: Path):
    db = tmp_path / "operator.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "b" * 40,
        }
    )
    result = run_durable_github_read_inspection(
        frontier=_frontier(),
        target_subject=TARGET,
        state_db=db,
        lineage_id="operator-test",
        holder="unit-test",
        lease_ttl=60.0,
        registry_digest="d" * 64,
        authorized_target_repositories=("example/consumer",),
        authorized_provider_repositories=("example/provider",),
        token=None,
        transport=transport,
        clock=_clock,
    )

    assert result.status is WorkUnitStatus.COMPLETE
    assert result.backend_classification == "SUCCEEDED"
    assert result.fencing_token == 1
    assert result.budget_generation == 2
    assert result.work_generation == 5
    assert transport.calls == [
        ("example/provider", "main"),
        ("example/consumer", "main"),
        ("example/provider", "main"),
        ("example/consumer", "main"),
    ]
    assert summarize_recovery_state(db, now=2.0) == {
        "unresolved": 0,
        "live_fences": 0,
        "actions": {},
        "phases": {},
    }

    budgets = SqliteBudgetStore(db)
    budget, generation = budgets.get("operator-test")
    budgets.close()
    assert generation == 2
    assert budget.remaining_active == 1
    assert budget.remaining_backend_jobs == 1

    works = SqliteRecursiveWorkStore(db)
    stored = works.get("operator-test", result.work_fingerprint)
    works.close()
    assert stored is not None
    assert stored.work.status is WorkUnitStatus.COMPLETE
    assert stored.generation == 5


def test_durable_operator_fails_closed_when_exact_target_moved(tmp_path: Path):
    db = tmp_path / "operator-stale.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    result = run_durable_github_read_inspection(
        frontier=_frontier(),
        target_subject=TARGET,
        state_db=db,
        lineage_id="operator-stale",
        holder="unit-test",
        lease_ttl=60.0,
        registry_digest="e" * 64,
        authorized_target_repositories=("example/consumer",),
        authorized_provider_repositories=("example/provider",),
        token=None,
        transport=transport,
        clock=_clock,
    )

    assert result.status is WorkUnitStatus.SUPERSEDED
    assert result.backend_classification == "PRECONDITION_FAILED"
    assert summarize_recovery_state(db, now=2.0)["unresolved"] == 0


def test_durable_operator_does_not_silently_restart_existing_lineage(tmp_path: Path):
    db = tmp_path / "operator-repeat.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "b" * 40,
        }
    )
    kwargs = dict(
        frontier=_frontier(),
        target_subject=TARGET,
        state_db=db,
        lineage_id="operator-repeat",
        holder="unit-test",
        lease_ttl=60.0,
        registry_digest="f" * 64,
        authorized_target_repositories=("example/consumer",),
        authorized_provider_repositories=("example/provider",),
        token=None,
        transport=transport,
        clock=_clock,
    )
    first = run_durable_github_read_inspection(**kwargs)
    assert first.status is WorkUnitStatus.COMPLETE

    with pytest.raises(ValueError, match="root execution state already exists"):
        run_durable_github_read_inspection(**kwargs)


def test_recovery_summary_for_missing_database_is_empty(tmp_path: Path):
    assert summarize_recovery_state(
        tmp_path / "missing.sqlite3",
        now=1.0,
        detailed=True,
    ) == {
        "unresolved": 0,
        "live_fences": 0,
        "actions": {},
        "phases": {},
        "items": [],
    }


def test_atomic_root_initialization_rolls_back_budget_when_work_insert_collides(
    tmp_path: Path,
):
    db = tmp_path / "atomic-root.sqlite3"
    frontier = _frontier()
    work = frontier_to_github_inspection_work(
        frontier,
        TARGET,
        registry_digest="1" * 64,
    )

    works = SqliteRecursiveWorkStore(db)
    works.put_initial(
        work=work,
        lineage_id="atomic-root",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={result_fingerprint := __import__(
            "runner.work_units",
            fromlist=["work_unit_fingerprint"],
        ).work_unit_fingerprint(work)},
        effective_capabilities={"read", "analyze"},
    )
    works.close()
    assert result_fingerprint

    budget = BudgetEnvelope(
        lineage_id="atomic-root",
        max_depth=1,
        depth=0,
        remaining_children=0,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
    )
    dispatch = SqliteDispatchAdmissionStore(db)
    with pytest.raises(ValueError, match="root execution state already exists"):
        dispatch.initialize_root(
            budget=budget,
            work=work,
            effective_capabilities={"read", "analyze"},
        )
    dispatch.close()

    budgets = SqliteBudgetStore(db)
    with pytest.raises(KeyError):
        budgets.get("atomic-root")
    budgets.close()


def test_durable_operator_rejects_target_outside_frontier_project_scope(
    tmp_path: Path,
):
    db = tmp_path / "operator-authority.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "b" * 40,
        }
    )

    with pytest.raises(
        ValueError,
        match="target repository is not authorized",
    ):
        run_durable_github_read_inspection(
            frontier=_frontier(),
            target_subject=TARGET,
            state_db=db,
            lineage_id="operator-authority",
            holder="unit-test",
            lease_ttl=60.0,
            registry_digest="2" * 64,
            authorized_target_repositories=("example/other",),
            authorized_provider_repositories=("example/provider",),
            token=None,
            transport=transport,
            clock=_clock,
        )

    assert transport.calls == []
    assert not db.exists()


def test_durable_operator_rejects_unregistered_provider_before_transport(
    tmp_path: Path,
):
    db = tmp_path / "operator-provider-authority.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "b" * 40,
        }
    )

    with pytest.raises(
        ValueError,
        match="provider repository is not registered",
    ):
        run_durable_github_read_inspection(
            frontier=_frontier(),
            target_subject=TARGET,
            state_db=db,
            lineage_id="operator-provider-authority",
            holder="unit-test",
            lease_ttl=60.0,
            registry_digest="3" * 64,
            authorized_target_repositories=("example/consumer",),
            authorized_provider_repositories=("example/other-provider",),
            token=None,
            transport=transport,
            clock=_clock,
        )

    assert transport.calls == []
    assert not db.exists()
