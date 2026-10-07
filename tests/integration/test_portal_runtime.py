from __future__ import annotations

from pathlib import Path

from portal.models import ExecutionNode
from portal.runtime import PortalRunStore, run_portal_once
from runner.models import DependencyEdge, ProjectDefinition
from runner.portfolio import collect_and_schedule_portfolio


class FakeTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.calls = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.calls.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository: str, path: str, ref: str):
        raise AssertionError("portal runtime test is ref-read only")

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("portal runtime must not create branches")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str:
        raise AssertionError("portal runtime must not write files")


def _project(project_id: str, repository: str, *, target: bool) -> ProjectDefinition:
    payload = {
        "id": project_id,
        "name": project_id,
        "visibility": "public",
        "repositories": [repository],
        "capabilities": ["read", "analyze"],
        "assignment_scope": "NONE",
        "review_scope": "NONE",
        "family_id": project_id,
        "scheduling_state": "SCHEDULABLE",
    }
    if target:
        payload["execution_targets"] = [
            {
                "work_type": "INSPECT",
                "repository": repository,
                "ref": "main",
            }
        ]
    return ProjectDefinition.from_mapping(payload)


def _dependency(
    dependency_id: str,
    *,
    provider: str,
    consumer: str,
    provider_repository: str,
) -> DependencyEdge:
    return DependencyEdge.from_mapping(
        {
            "id": dependency_id,
            "provider": provider,
            "consumer": consumer,
            "kind": "source",
            "selector": {
                "repository": provider_repository,
                "ref": "main",
            },
            "reaction": "INSPECT",
            "evidence": "exact-subject",
        }
    )


def _fixture():
    projects = (
        _project("provider-a", "example/provider-a", target=False),
        _project("consumer-a", "example/consumer-a", target=True),
        _project("provider-b", "example/provider-b", target=False),
        _project("consumer-b", "example/consumer-b", target=True),
    )
    dependencies = (
        _dependency(
            "a-to-a",
            provider="provider-a",
            consumer="consumer-a",
            provider_repository="example/provider-a",
        ),
        _dependency(
            "b-to-b",
            provider="provider-b",
            consumer="consumer-b",
            provider_repository="example/provider-b",
        ),
    )
    transport = FakeTransport(
        {
            ("example/provider-a", "main"): "a" * 40,
            ("example/provider-b", "main"): "b" * 40,
            ("example/consumer-a", "main"): "c" * 40,
            ("example/consumer-b", "main"): "d" * 40,
        }
    )
    return projects, dependencies, transport


def test_run_once_executes_independent_ready_work_across_node_slots(
    tmp_path: Path,
) -> None:
    projects, dependencies, transport = _fixture()
    db = tmp_path / "portal.sqlite3"

    baseline = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependencies,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    assert baseline.baseline is True

    transport.heads[("example/provider-a", "main")] = "e" * 40
    transport.heads[("example/provider-b", "main")] = "f" * 40

    result = run_portal_once(
        projects=projects,
        dependencies=dependencies,
        workers=(),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        nodes=(
            ExecutionNode(node_id="alpha", max_parallel=1),
            ExecutionNode(node_id="beta", max_parallel=1),
        ),
        run_id="portfolio-run",
        holder="vera",
        lease_ttl=60.0,
        max_parallel=2,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    assert result.cycle.baseline is False
    assert result.cycle.ready_count == 2
    assert len(result.lanes) == 2
    assert {lane.node_id for lane in result.lanes} == {"alpha", "beta"}
    assert {lane.queue_state for lane in result.lanes} == {"COMPLETE"}
    assert all(lane.claimed for lane in result.lanes)
    assert result.queue_summary["states"] == {"COMPLETE": 2}
    assert result.progress_made is True

    store = PortalRunStore(db)
    try:
        summary = store.summary("portfolio-run")
    finally:
        store.close()

    assert summary["run_id"] == "portfolio-run"
    assert summary["cycles"] == 1
    assert summary["lane_events"] == 2
    assert summary["states"] == {"COMPLETE": 2}


def test_run_once_is_bounded_by_node_capacity(tmp_path: Path) -> None:
    projects, dependencies, transport = _fixture()
    db = tmp_path / "portal-capacity.sqlite3"

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependencies,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider-a", "main")] = "e" * 40
    transport.heads[("example/provider-b", "main")] = "f" * 40

    result = run_portal_once(
        projects=projects,
        dependencies=dependencies,
        workers=(),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        nodes=(ExecutionNode(node_id="only", max_parallel=1),),
        run_id="bounded-run",
        holder="vera",
        lease_ttl=60.0,
        max_parallel=8,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    assert len(result.lanes) == 1
    assert result.lanes[0].node_id == "only"
    assert result.lanes[0].queue_state == "COMPLETE"
    assert result.queue_summary["states"]["COMPLETE"] == 1


def test_run_once_rejects_duplicate_run_configuration(tmp_path: Path) -> None:
    projects, dependencies, transport = _fixture()
    db = tmp_path / "portal-config.sqlite3"

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependencies,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )

    common = dict(
        projects=projects,
        dependencies=dependencies,
        workers=(),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        run_id="same-run",
        holder="vera",
        lease_ttl=60.0,
        max_parallel=2,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    run_portal_once(
        **common,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
    )

    import pytest

    with pytest.raises(ValueError, match="run configuration changed"):
        run_portal_once(
            **common,
            nodes=(ExecutionNode(node_id="beta", max_parallel=1),),
        )



def test_continuous_run_refills_lane_until_portfolio_is_idle(
    tmp_path: Path,
) -> None:
    from portal.runtime import run_portal_until_idle

    projects, dependencies, transport = _fixture()
    db = tmp_path / "portal-refill.sqlite3"

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependencies,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider-a", "main")] = "e" * 40
    transport.heads[("example/provider-b", "main")] = "f" * 40

    ticks = iter(float(value) for value in range(2, 100))
    result = run_portal_until_idle(
        projects=projects,
        dependencies=dependencies,
        workers=(),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        nodes=(ExecutionNode(node_id="only", max_parallel=1),),
        run_id="refill-run",
        holder="vera",
        lease_ttl=60.0,
        max_parallel=1,
        max_cycles=10,
        max_idle_cycles=1,
        poll_seconds=0.0,
        token=None,
        transport=transport,
        clock=lambda: next(ticks),
        sleep=lambda _seconds: None,
    )

    assert result.stop_reason == "IDLE"
    assert len(result.cycles) == 3
    assert [cycle.progress_made for cycle in result.cycles] == [
        True,
        True,
        False,
    ]
    completed = [
        lane
        for cycle in result.cycles
        for lane in cycle.lanes
        if lane.claimed and lane.queue_state == "COMPLETE"
    ]
    assert len(completed) == 2

    store = PortalRunStore(db)
    try:
        summary = store.summary("refill-run")
    finally:
        store.close()
    assert summary["cycles"] == 3
    assert summary["lane_events"] == 2
    assert summary["states"] == {"COMPLETE": 2}
