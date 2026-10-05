from pathlib import Path

import pytest

from runner.models import DependencyEdge, ProjectDefinition, WorkerDefinition
from runner.portfolio import (
    SqlitePortfolioStore,
    collect_and_schedule_portfolio,
    summarize_portfolio_state,
)


class FakeTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.calls = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.calls.append((repository, ref))
        value = self.heads[(repository, ref)]
        if isinstance(value, Exception):
            raise value
        return value

    def read_file(self, repository: str, path: str, ref: str):
        raise AssertionError("portfolio V1 does not read files")

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("portfolio V1 is read-only")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str:
        raise AssertionError("portfolio V1 is read-only")


def _project(
    project_id: str,
    repository: str,
    *,
    scheduling_state: str = "SCHEDULABLE",
    execution_target: bool | None = None,
) -> ProjectDefinition:
    if execution_target is None:
        execution_target = project_id == "consumer"
    payload = {
        "id": project_id,
        "name": project_id,
        "visibility": "public",
        "repositories": [repository],
        "capabilities": ["read", "analyze"],
        "assignment_scope": "NONE",
        "review_scope": "NONE",
        "family_id": project_id,
        "scheduling_state": scheduling_state,
    }
    if execution_target:
        payload["execution_targets"] = [
            {
                "work_type": "INSPECT",
                "repository": repository,
                "ref": "main",
            }
        ]
    return ProjectDefinition.from_mapping(payload)


def _dependency(
    *,
    repository: str = "example/provider",
    ref: str = "main",
    path_prefix: str | None = "src/",
    reaction: str = "INSPECT",
) -> DependencyEdge:
    selector = {"repository": repository, "ref": ref}
    if path_prefix is not None:
        selector["path_prefix"] = path_prefix
    return DependencyEdge.from_mapping(
        {
            "id": "provider-to-consumer",
            "provider": "provider",
            "consumer": "consumer",
            "kind": "source",
            "selector": selector,
            "reaction": reaction,
            "evidence": "exact-subject",
        }
    )


def _run(
    db: Path,
    transport: FakeTransport,
    *,
    consumer_state: str = "SCHEDULABLE",
    registry_digest: str = "1" * 64,
    dependency_digest: str = "2" * 64,
):
    return collect_and_schedule_portfolio(
        projects=(
            _project("provider", "example/provider"),
            _project(
                "consumer",
                "example/consumer",
                scheduling_state=consumer_state,
            ),
        ),
        dependencies=(_dependency(),),
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )


def test_first_cycle_is_baseline_and_second_change_queues_ready_frontier(
    tmp_path: Path,
):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )

    baseline = _run(db, transport)
    assert baseline.baseline is True
    assert baseline.observation_count == 1
    assert baseline.changed_count == 0
    assert baseline.frontier_count == 0
    assert baseline.queued_count == 0

    transport.heads[("example/provider", "main")] = "b" * 40
    changed = _run(db, transport)

    assert changed.baseline is False
    assert changed.changed_count == 1
    assert changed.frontier_count == 1
    assert changed.ready_count == 1
    assert changed.blocked_count == 0
    assert changed.queued_count == 1

    summary = summarize_portfolio_state(db)
    assert summary["snapshots"] == 2
    assert summary["ready"] == 1
    assert summary["queued_total"] == 1

    store = SqlitePortfolioStore(db)
    try:
        row = store.connection.execute(
            """
            SELECT status, queue_state, subject_commit, subject_path
            FROM portfolio_frontiers
            WHERE snapshot_id = ?
            """,
            (changed.snapshot_id,),
        ).fetchone()
    finally:
        store.close()

    assert row == ("READY", "QUEUED", "b" * 40, "src/")


def test_no_change_creates_snapshot_without_duplicate_work(tmp_path: Path):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )

    _run(db, transport)
    unchanged = _run(db, transport)

    assert unchanged.baseline is False
    assert unchanged.changed_count == 0
    assert unchanged.frontier_count == 0
    assert unchanged.queued_count == 0


def test_held_consumer_is_persisted_blocked_not_queued(tmp_path: Path):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )

    _run(db, transport, consumer_state="HELD")
    transport.heads[("example/provider", "main")] = "b" * 40
    result = _run(db, transport, consumer_state="HELD")

    assert result.frontier_count == 1
    assert result.ready_count == 0
    assert result.blocked_count == 1
    assert result.queued_count == 0

    store = SqlitePortfolioStore(db)
    try:
        row = store.connection.execute(
            """
            SELECT status, queue_state
            FROM portfolio_frontiers
            WHERE snapshot_id = ?
            """,
            (result.snapshot_id,),
        ).fetchone()
    finally:
        store.close()

    assert row == ("WAITING_SCHEDULING", "BLOCKED")


def test_dependency_provider_scope_is_validated_before_transport_or_state(
    tmp_path: Path,
):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("attacker/repository", "main"): "a" * 40}
    )

    with pytest.raises(
        ValueError,
        match="outside its provider project scope",
    ):
        collect_and_schedule_portfolio(
            projects=(
                _project("provider", "example/provider"),
                _project("consumer", "example/consumer"),
            ),
            dependencies=(
                _dependency(repository="attacker/repository"),
            ),
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            state_db=db,
            token=None,
            transport=transport,
        )

    assert transport.calls == []
    assert not db.exists()


def test_transport_failure_does_not_advance_durable_snapshot(tmp_path: Path):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    first = _run(db, transport)

    transport.heads[("example/provider", "main")] = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _run(db, transport)

    summary = summarize_portfolio_state(db)
    assert summary["snapshots"] == 1
    assert summary["latest_snapshot_id"] == first.snapshot_id


def test_dependency_digest_change_establishes_new_baseline(tmp_path: Path):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )

    _run(db, transport, dependency_digest="2" * 64)
    transport.heads[("example/provider", "main")] = "b" * 40

    reset = _run(db, transport, dependency_digest="3" * 64)
    assert reset.baseline is True
    assert reset.changed_count == 0
    assert reset.frontier_count == 0
    assert reset.queued_count == 0


def test_exact_ref_is_required_for_currentness_collection(tmp_path: Path):
    db = tmp_path / "portfolio.sqlite3"
    transport = FakeTransport({})

    with pytest.raises(
        ValueError,
        match="requires an exact dependency ref",
    ):
        collect_and_schedule_portfolio(
            projects=(
                _project("provider", "example/provider"),
                _project("consumer", "example/consumer"),
            ),
            dependencies=(
                _dependency(ref=""),
            ),
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            state_db=db,
            token=None,
            transport=transport,
        )

    assert transport.calls == []
    assert not db.exists()



def test_schedulable_consumer_without_execution_target_waits_for_authority(
    tmp_path: Path,
):
    db = tmp_path / "portfolio-no-target.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    projects = (
        _project("provider", "example/provider"),
        _project(
            "consumer",
            "example/consumer",
            execution_target=False,
        ),
    )

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="4" * 64,
        dependency_digest="5" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    result = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="4" * 64,
        dependency_digest="5" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 101.0,
    )

    assert result.ready_count == 0
    assert result.blocked_count == 1
    assert result.queued_count == 0

    store = SqlitePortfolioStore(db)
    try:
        row = store.connection.execute(
            """
            SELECT status, queue_state
            FROM portfolio_frontiers
            WHERE snapshot_id = ?
            """,
            (result.snapshot_id,),
        ).fetchone()
    finally:
        store.close()
    assert row == ("WAITING_AUTHORITY", "BLOCKED")



def test_worker_registry_change_preserves_observation_continuity(tmp_path: Path):
    db = tmp_path / "portfolio-worker-digest.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    projects = (
        _project("provider", "example/provider"),
        _project("consumer", "example/consumer"),
    )

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="6" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40

    changed = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="7" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    assert changed.baseline is False
    assert changed.changed_count == 1
    assert changed.frontier_count == 1
    assert changed.queued_count == 1


def test_verified_worker_route_wakes_existing_waiting_authority_frontier(
    tmp_path: Path,
):
    db = tmp_path / "portfolio-worker-authority.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    provider = _project("provider", "example/provider")
    consumer = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": "example/consumer",
                    "ref": "review",
                    "worker_id": "reviewer",
                    "worker_route": "OPENAI_AGENT_API",
                }
            ],
        }
    )
    registered = WorkerDefinition.from_mapping(
        {
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "REGISTERED",
            "locators": {"model": "reviewer-model"},
            "roles": ["review"],
            "routes": {"OPENAI_AGENT_API": "UNVERIFIED"},
        }
    )
    executable = WorkerDefinition.from_mapping(
        {
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "EXECUTABLE",
            "locators": {"model": "reviewer-model"},
            "roles": ["review"],
            "routes": {"OPENAI_AGENT_API": "VERIFIED"},
            "route_contracts": {
                "OPENAI_AGENT_API": {
                    "effect_class": "READ_ONLY",
                    "replay_policy": "SAFE",
                }
            },
            "reconstruction": {
                "repository": "example/workers",
                "path": "reviewer.md",
                "commit": "f" * 40,
            },
        }
    )
    projects = (provider, consumer)
    dependency = (_dependency(path_prefix=None, reaction="REREVIEW"),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=(registered,),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="6" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    blocked = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=(registered,),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="6" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    assert blocked.ready_count == 0
    assert blocked.blocked_count == 1

    awakened = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=(executable,),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="7" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )

    assert awakened.baseline is False
    assert awakened.changed_count == 0
    assert awakened.frontier_count == 1
    assert awakened.ready_count == 1
    assert awakened.queued_count == 1



def test_portfolio_status_deduplicates_carried_queue_history(tmp_path: Path):
    db = tmp_path / "portfolio-status-dedup.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    projects = (
        _project("provider", "example/provider"),
        _project("consumer", "example/consumer"),
    )

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    changed = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    assert changed.queued_count == 1

    carried = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert carried.queued_count == 1

    summary = summarize_portfolio_state(db)
    assert summary["snapshots"] == 3
    assert summary["queued_total"] == 1



def test_topology_predecessor_cas_rejects_concurrent_config_fork(tmp_path: Path):
    db = tmp_path / "portfolio-topology-cas.sqlite3"
    transport = FakeTransport(
        {("example/provider", "main"): "a" * 40}
    )
    projects = (
        _project("provider", "example/provider"),
        _project("consumer", "example/consumer"),
    )

    baseline = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    assert baseline.snapshot_id == 1

    first = SqlitePortfolioStore(db)
    second = SqlitePortfolioStore(db)
    try:
        observations = first.load_observations(baseline.snapshot_id)
        assert first.latest_snapshot_id_for_dependency(
            dependency_digest="2" * 64
        ) == baseline.snapshot_id

        advanced = first.commit_cycle(
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            worker_registry_digest="4" * 64,
            snapshot_digest="a" * 64,
            observed_at=2.0,
            baseline=False,
            observations=observations,
            changed_count=0,
            ranked_frontiers=(),
            expected_previous_snapshot_id=None,
            expected_previous_dependency_snapshot_id=baseline.snapshot_id,
        )
        assert advanced == 2

        with pytest.raises(
            RuntimeError,
            match="topology currentness changed concurrently",
        ):
            second.commit_cycle(
                registry_digest="1" * 64,
                dependency_digest="2" * 64,
                worker_registry_digest="5" * 64,
                snapshot_digest="b" * 64,
                observed_at=2.1,
                baseline=False,
                observations=observations,
                changed_count=0,
                ranked_frontiers=(),
                expected_previous_snapshot_id=None,
                expected_previous_dependency_snapshot_id=baseline.snapshot_id,
            )
    finally:
        first.close()
        second.close()
