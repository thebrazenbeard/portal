from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.m6_github import frontier_to_github_inspection_work
from runner.models import (
    DependencyEdge,
    ExactSubject,
    InvocationRoute,
    ProjectDefinition,
    WorkerDefinition,
)
from runner.portfolio import collect_and_schedule_portfolio
from runner.queue_consumer import (
    SqliteQueueStore,
    consume_next_queued_inspection,
    consume_next_queued_read_only_work,
    reconcile_queue_item,
    summarize_queue_state,
)
from runner.work_units import WorkUnitStatus
from runner.worker_routing import (
    SqliteWorkerRouteStore,
    resolve_read_only_worker_route,
)


class FakeTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.calls = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.calls.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository: str, path: str, ref: str):
        raise AssertionError("queue bridge is ref-read only")

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("queue bridge is read-only")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str:
        raise AssertionError("queue bridge is read-only")


def _projects(*, target_ref: str = "main"):
    provider = ProjectDefinition.from_mapping(
        {
            "id": "provider",
            "name": "Provider",
            "visibility": "public",
            "repositories": ["example/provider"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "provider",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    consumer = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "INSPECT",
                    "repository": "example/consumer",
                    "ref": target_ref,
                }
            ],
        }
    )
    return provider, consumer


def _dependency():
    return DependencyEdge.from_mapping(
        {
            "id": "provider-to-consumer",
            "provider": "provider",
            "consumer": "consumer",
            "kind": "source",
            "selector": {
                "repository": "example/provider",
                "ref": "main",
            },
            "reaction": "INSPECT",
            "evidence": "exact-subject",
        }
    )


def _schedule(
    db: Path,
    transport: FakeTransport,
    *,
    registry_digest: str = "1" * 64,
    dependency_digest: str = "2" * 64,
    target_ref: str = "main",
):
    projects = _projects(target_ref=target_ref)
    dependency = (_dependency(),)
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    changed = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    assert changed.queued_count == 1
    return projects, changed


def test_queue_consumer_executes_declared_target_and_completes(tmp_path: Path):
    db = tmp_path / "queue.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, changed = _schedule(db, transport)
    transport.calls.clear()

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="queue-test",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )

    assert result.claimed is True
    assert result.queue_state == "COMPLETE"
    assert result.snapshot_id == changed.snapshot_id
    assert result.fencing_token == 1
    assert result.operator_status == "COMPLETE"
    assert transport.calls == [
        ("example/consumer", "main"),
        ("example/provider", "main"),
        ("example/consumer", "main"),
        ("example/provider", "main"),
        ("example/consumer", "main"),
    ]
    assert summarize_queue_state(db) == {
        "claims": 1,
        "states": {"COMPLETE": 1},
    }


def test_queue_consumer_uses_declared_nondefault_ref(tmp_path: Path):
    db = tmp_path / "queue-target-ref.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects, _changed = _schedule(
        db,
        transport,
        target_ref="review",
    )
    transport.calls.clear()

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="queue-test",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )

    assert result.queue_state == "COMPLETE"
    assert ("example/consumer", "review") in transport.calls
    assert ("example/consumer", "main") not in transport.calls


def test_queue_claim_is_fenced_and_reclaim_increments_token(tmp_path: Path):
    db = tmp_path / "queue-fence.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    try:
        first = store.claim_next(
            projects=projects,
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            holder="one",
            now=3.0,
            ttl=10.0,
        )
        assert first is not None
        assert first.fencing_token == 1

        blocked = store.claim_next(
            projects=projects,
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            holder="two",
            now=4.0,
            ttl=10.0,
        )
        assert blocked is None

        reclaimed = store.claim_next(
            projects=projects,
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            holder="two",
            now=14.0,
            ttl=10.0,
        )
        assert reclaimed is not None
        assert reclaimed.frontier_fingerprint == first.frontier_fingerprint
        assert reclaimed.fencing_token == 2
    finally:
        store.close()


def test_unchanged_newer_snapshot_does_not_erase_pending_queue_work(
    tmp_path: Path,
):
    db = tmp_path / "queue-current-snapshot.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, changed = _schedule(db, transport)
    unchanged = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert unchanged.frontier_count == 1
    assert unchanged.queued_count == 1
    transport.calls.clear()

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="queue-test",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 4.0,
    )

    assert result.claimed is True
    assert result.queue_state == "COMPLETE"
    assert result.snapshot_id == unchanged.snapshot_id


def test_newer_provider_subject_supersedes_old_unclaimed_queue_candidate(
    tmp_path: Path,
):
    db = tmp_path / "queue-newer-subject.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "d" * 40,
        }
    )
    projects, first_change = _schedule(db, transport)
    transport.heads[("example/provider", "main")] = "c" * 40
    newer = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert newer.queued_count == 1
    assert newer.snapshot_id != first_change.snapshot_id

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="queue-test",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 4.0,
    )

    assert result.claimed is True
    assert result.queue_state == "COMPLETE"
    assert result.snapshot_id == newer.snapshot_id


def test_reclaimed_queue_reconciles_terminal_operator_without_reexecution(
    tmp_path: Path,
):
    db = tmp_path / "queue-reconcile.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=5.0,
    )
    assert claim is not None
    claim = store.bind_target_head(
        claim,
        target_head="c" * 40,
        now=3.1,
    )
    store.close()

    from runner.operator import run_durable_github_read_inspection

    operator_result = run_durable_github_read_inspection(
        frontier=claim.frontier,
        target_subject=ExactSubject(
            repository=claim.target_repository,
            ref=claim.target_ref,
            commit=claim.target_head,
        ),
        state_db=db,
        lineage_id=claim.operator_lineage,
        holder="first-holder",
        lease_ttl=60.0,
        registry_digest="1" * 64,
        authorized_target_repositories=("example/consumer",),
        authorized_provider_repositories=("example/provider",),
        token=None,
        transport=transport,
        clock=lambda: 3.2,
    )
    assert operator_result.status is WorkUnitStatus.COMPLETE
    transport.calls.clear()

    recovered = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="second-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 9.0,
    )

    assert recovered.queue_state == "COMPLETE"
    assert recovered.fencing_token == 2
    assert recovered.operator_status == "COMPLETE"
    assert transport.calls == []


def test_reclaimed_queue_refuses_nonterminal_operator_reexecution(tmp_path: Path):
    db = tmp_path / "queue-ambiguous.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=5.0,
    )
    assert claim is not None
    claim = store.bind_target_head(
        claim,
        target_head="c" * 40,
        now=3.1,
    )
    store.close()

    work = frontier_to_github_inspection_work(
        claim.frontier,
        ExactSubject(
            repository=claim.target_repository,
            ref=claim.target_ref,
            commit=claim.target_head,
        ),
        registry_digest="1" * 64,
    )
    dispatch = SqliteDispatchAdmissionStore(db)
    dispatch.initialize_root(
        budget=BudgetEnvelope(
            lineage_id=claim.operator_lineage,
            max_depth=1,
            depth=0,
            remaining_children=0,
            remaining_active=2,
            remaining_retries=1,
            remaining_backend_jobs=2,
        ),
        work=work,
        effective_capabilities={"read", "analyze"},
    )
    dispatch.close()
    transport.calls.clear()

    recovered = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="second-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 9.0,
    )

    assert recovered.queue_state == "OUTCOME_UNKNOWN"
    assert recovered.operator_status == "PENDING"
    assert "re-execution was not attempted" in recovered.reason
    assert transport.calls == []



def test_ambiguous_old_claim_blocks_newer_colliding_frontier(tmp_path: Path):
    db = tmp_path / "queue-ambiguous-collision.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    first = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=10.0,
    )
    assert first is not None
    store.finalize(
        first,
        state="OUTCOME_UNKNOWN",
        reason="simulated ambiguous prior operator state",
        now=3.1,
    )
    store.close()

    transport.heads[("example/provider", "main")] = "d" * 40
    newer = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 4.0,
    )
    assert newer.queued_count == 1
    transport.calls.clear()

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="second-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 5.0,
    )

    assert result.claimed is True
    assert result.queue_state == "COMPLETE"
    assert result.snapshot_id == newer.snapshot_id
    assert transport.calls



def test_reclaim_rejects_persisted_lineage_divergence(tmp_path: Path):
    db = tmp_path / "queue-lineage-tamper.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=5.0,
    )
    assert claim is not None
    store.connection.execute(
        """
        UPDATE portfolio_queue_claims
        SET operator_lineage = 'tampered-lineage'
        WHERE snapshot_id = ? AND frontier_fingerprint = ?
        """,
        (claim.snapshot_id, claim.frontier_fingerprint),
    )

    with pytest.raises(
        ValueError,
        match="lineage diverges",
    ):
        store.claim_next(
            projects=projects,
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            holder="second-holder",
            now=9.0,
            ttl=5.0,
        )
    store.close()


def test_reclaim_rejects_persisted_target_binding_divergence(tmp_path: Path):
    db = tmp_path / "queue-target-tamper.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=5.0,
    )
    assert claim is not None
    store.connection.execute(
        """
        UPDATE portfolio_queue_claims
        SET target_ref = 'tampered-ref'
        WHERE snapshot_id = ? AND frontier_fingerprint = ?
        """,
        (claim.snapshot_id, claim.frontier_fingerprint),
    )

    with pytest.raises(
        ValueError,
        match="target diverges",
    ):
        store.claim_next(
            projects=projects,
            registry_digest="1" * 64,
            dependency_digest="2" * 64,
            holder="second-holder",
            now=9.0,
            ttl=5.0,
        )
    store.close()



def test_outcome_unknown_can_be_evidence_released_for_safe_read_retry(
    tmp_path: Path,
):
    db = tmp_path / "queue-reconcile-retry.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=20.0,
    )
    assert claim is not None
    store.finalize(
        claim,
        state="OUTCOME_UNKNOWN",
        reason="simulated ambiguous read",
        now=4.0,
    )
    store.close()

    reconciled = reconcile_queue_item(
        state_db=db,
        snapshot_id=claim.snapshot_id,
        frontier_fingerprint_value=claim.frontier_fingerprint,
        expected_fencing_token=claim.fencing_token,
        resolution="RELEASE_RETRY_READ_ONLY",
        evidence_sha256="e" * 64,
        reconciler="test-reconciler",
        clock=lambda: 5.0,
    )
    assert reconciled.previous_state == "OUTCOME_UNKNOWN"
    assert reconciled.final_state == "FAILED_RETRYABLE"
    assert reconciled.attempt_generation == 2

    store = SqliteQueueStore(db)
    retry = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="second-holder",
        now=6.0,
        ttl=20.0,
    )
    store.close()
    assert retry is not None
    assert retry.fencing_token == claim.fencing_token + 1
    assert retry.attempt_generation == 2
    assert retry.operator_lineage.endswith(":retry:2")


def test_reconciliation_requires_exact_fencing_token(tmp_path: Path):
    db = tmp_path / "queue-reconcile-fence.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, _changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="holder",
        now=3.0,
        ttl=20.0,
    )
    assert claim is not None
    store.finalize(
        claim,
        state="OUTCOME_UNKNOWN",
        reason="ambiguous",
        now=4.0,
    )
    store.close()

    with pytest.raises(ValueError, match="fencing token mismatch"):
        reconcile_queue_item(
            state_db=db,
            snapshot_id=claim.snapshot_id,
            frontier_fingerprint_value=claim.frontier_fingerprint,
            expected_fencing_token=claim.fencing_token + 1,
            resolution="CONFIRM_SUPERSEDED",
            evidence_sha256="e" * 64,
            reconciler="test-reconciler",
            clock=lambda: 5.0,
        )


def _read_only_worker_fixture(*, replay_policy: str = "SAFE"):
    return WorkerDefinition.from_mapping(
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
                    "replay_policy": replay_policy,
                }
            },
            "reconstruction": {
                "repository": "example/workers",
                "path": "reviewer.md",
                "commit": "f" * 40,
            },
        }
    )


def _rereview_projects():
    provider = ProjectDefinition.from_mapping(
        {
            "id": "provider",
            "name": "Provider",
            "visibility": "public",
            "repositories": ["example/provider"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "provider",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    consumer = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
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
    return provider, consumer


def _rereview_dependency():
    return DependencyEdge.from_mapping(
        {
            "id": "provider-review",
            "provider": "provider",
            "consumer": "consumer",
            "kind": "review",
            "selector": {
                "repository": "example/provider",
                "ref": "main",
            },
            "reaction": "REREVIEW",
            "evidence": "exact-subject",
        }
    )


def test_verified_read_only_worker_work_routes_to_durable_outbox_atomically(
    tmp_path: Path,
):
    db = tmp_path / "queue-worker-route.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    changed = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    assert changed.queued_count == 1

    result = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert result.queue_state == "ROUTED"
    assert result.route_id is not None

    store = SqliteQueueStore(db)
    try:
        queue_row = store.connection.execute(
            """
            SELECT state
            FROM portfolio_queue_claims
            WHERE snapshot_id = ? AND frontier_fingerprint = ?
            """,
            (result.snapshot_id, result.frontier_fingerprint),
        ).fetchone()
        route_row = store.connection.execute(
            """
            SELECT state, worker_id, invocation_route, replay_policy
            FROM worker_route_outbox
            WHERE route_id = ?
            """,
            (result.route_id,),
        ).fetchone()
    finally:
        store.close()

    assert queue_row == ("ROUTED",)
    assert route_row == (
        "PENDING",
        "reviewer",
        "OPENAI_AGENT_API",
        "SAFE",
    )


def test_routed_worker_reconciliation_closes_outbox_and_releases_retry(
    tmp_path: Path,
):
    db = tmp_path / "queue-worker-reconcile.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert routed.queue_state == "ROUTED"

    route_store = SqliteWorkerRouteStore(db)
    delivery = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-delivery",
        now=3.1,
        ttl=30.0,
        worker_registry_digest="3" * 64,
    )
    assert delivery is not None
    route_store.record_receipt(
        route_id=routed.route_id,
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        holder="worker-delivery",
        expected_fencing_token=delivery.fencing_token,
        receipt_class="FAILED_RETRYABLE",
        receipt_sha256="8" * 64,
        now=3.2,
    )
    route_store.close()

    reconciled = reconcile_queue_item(
        state_db=db,
        snapshot_id=routed.snapshot_id,
        frontier_fingerprint_value=routed.frontier_fingerprint,
        expected_fencing_token=routed.fencing_token,
        resolution="RELEASE_RETRY_READ_ONLY",
        evidence_sha256="9" * 64,
        reconciler="test-reconciler",
        clock=lambda: 4.0,
    )
    assert reconciled.final_state == "FAILED_RETRYABLE"

    store = SqliteQueueStore(db)
    try:
        outbox = store.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (routed.route_id,),
        ).fetchone()
    finally:
        store.close()
    assert outbox == ("RECONCILED_RETRY",)



def test_queue_visibility_requires_matching_worker_registry_digest(tmp_path: Path):
    db = tmp_path / "queue-worker-digest.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects = _projects()
    dependency = (_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
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
        dependencies=dependency,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="6" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    assert changed.queued_count == 1

    result = consume_next_queued_read_only_work(
        projects=projects,
        workers=(),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="7" * 64,
        state_db=db,
        holder="wrong-worker-registry",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert result.claimed is False
    assert result.queue_state == "NO_WORK"



def test_worker_route_digest_must_match_scheduling_snapshot(tmp_path: Path):
    db = tmp_path / "queue-worker-route-digest.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        holder="route-holder",
        now=3.0,
        ttl=60.0,
    )
    assert claim is not None
    claim = store.bind_target_head(
        claim,
        target_head="c" * 40,
        now=3.1,
    )
    route = resolve_read_only_worker_route(
        project=projects[1],
        frontier=claim.frontier,
        workers=workers,
    )
    assert route is not None

    with pytest.raises(ValueError, match="diverges from scheduling snapshot"):
        store.route_worker_claim(
            claim,
            worker_registry_digest="4" * 64,
            packet_sensitivity="PUBLIC",
            route=route,
            now=3.2,
        )
    store.close()


def test_routed_reconciliation_requires_worker_receipt(tmp_path: Path):
    db = tmp_path / "queue-route-receipt-required.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert routed.queue_state == "ROUTED"

    with pytest.raises(ValueError, match="requires a durable worker receipt"):
        reconcile_queue_item(
            state_db=db,
            snapshot_id=routed.snapshot_id,
            frontier_fingerprint_value=routed.frontier_fingerprint,
            expected_fencing_token=routed.fencing_token,
            resolution="CONFIRM_COMPLETE",
            evidence_sha256="a" * 64,
            reconciler="test-reconciler",
            clock=lambda: 4.0,
        )


def test_routed_reconciliation_rejects_incompatible_receipt_class(
    tmp_path: Path,
):
    db = tmp_path / "queue-route-receipt-class.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    route_store = SqliteWorkerRouteStore(db)
    delivery = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-delivery",
        now=3.1,
        ttl=30.0,
        worker_registry_digest="3" * 64,
    )
    assert delivery is not None
    route_store.record_receipt(
        route_id=routed.route_id,
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        holder="worker-delivery",
        expected_fencing_token=delivery.fencing_token,
        receipt_class="FAILED_RETRYABLE",
        receipt_sha256="8" * 64,
        now=3.2,
    )
    route_store.close()

    with pytest.raises(ValueError, match="incompatible with reconciliation"):
        reconcile_queue_item(
            state_db=db,
            snapshot_id=routed.snapshot_id,
            frontier_fingerprint_value=routed.frontier_fingerprint,
            expected_fencing_token=routed.fencing_token,
            resolution="CONFIRM_COMPLETE",
            evidence_sha256="a" * 64,
            reconciler="test-reconciler",
            clock=lambda: 4.0,
        )


def test_routed_reconciliation_requires_exact_outbox_envelope(tmp_path: Path):
    db = tmp_path / "queue-route-envelope-integrity.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert routed.queue_state == "ROUTED"

    store = SqliteQueueStore(db)
    store.connection.execute(
        "DELETE FROM worker_route_outbox WHERE route_id = ?",
        (routed.route_id,),
    )
    store.close()

    with pytest.raises(ValueError, match="lacks its durable worker-route envelope"):
        reconcile_queue_item(
            state_db=db,
            snapshot_id=routed.snapshot_id,
            frontier_fingerprint_value=routed.frontier_fingerprint,
            expected_fencing_token=routed.fencing_token,
            resolution="CONFIRM_COMPLETE",
            evidence_sha256="a" * 64,
            reconciler="test-reconciler",
            clock=lambda: 4.0,
        )



def test_failed_retryable_on_older_snapshot_is_recovered_into_latest_queue(
    tmp_path: Path,
):
    db = tmp_path / "queue-historical-retry.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "main"): "c" * 40,
        }
    )
    projects, changed = _schedule(db, transport)

    store = SqliteQueueStore(db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        holder="first-holder",
        now=3.0,
        ttl=60.0,
    )
    assert claim is not None
    assert claim.snapshot_id == changed.snapshot_id
    store.close()

    intervening = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 4.0,
    )
    assert intervening.frontier_count == 0

    store = SqliteQueueStore(db)
    store.finalize(
        claim,
        state="FAILED_RETRYABLE",
        reason="synthetic retryable failure",
        now=5.0,
    )
    store.close()

    recovered_snapshot = collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(_dependency(),),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 6.0,
    )
    assert recovered_snapshot.frontier_count == 1
    assert recovered_snapshot.queued_count == 1

    result = consume_next_queued_inspection(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        state_db=db,
        holder="second-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 7.0,
    )
    assert result.claimed is True
    assert result.queue_state == "COMPLETE"
    assert result.snapshot_id == recovered_snapshot.snapshot_id



def test_routed_success_receipt_requires_fresh_provider_and_target_for_complete(
    tmp_path: Path,
):
    db = tmp_path / "queue-worker-complete-currentness.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert routed.queue_state == "ROUTED"

    route_store = SqliteWorkerRouteStore(db)
    delivery = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-delivery",
        now=3.1,
        ttl=30.0,
        worker_registry_digest="3" * 64,
    )
    assert delivery is not None
    route_store.record_receipt(
        route_id=routed.route_id,
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        holder="worker-delivery",
        expected_fencing_token=delivery.fencing_token,
        receipt_class="SUCCEEDED",
        receipt_sha256="8" * 64,
        now=3.2,
    )
    route_store.close()

    completed = reconcile_queue_item(
        state_db=db,
        snapshot_id=routed.snapshot_id,
        frontier_fingerprint_value=routed.frontier_fingerprint,
        expected_fencing_token=routed.fencing_token,
        resolution="CONFIRM_COMPLETE",
        evidence_sha256="9" * 64,
        reconciler="test-reconciler",
        transport=transport,
        clock=lambda: 4.0,
    )
    assert completed.final_state == "COMPLETE"

    store = SqliteQueueStore(db)
    try:
        queue_state = store.connection.execute(
            """
            SELECT state
            FROM portfolio_queue_claims
            WHERE snapshot_id = ? AND frontier_fingerprint = ?
            """,
            (routed.snapshot_id, routed.frontier_fingerprint),
        ).fetchone()
        route_state = store.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (routed.route_id,),
        ).fetchone()
    finally:
        store.close()
    assert queue_state == ("COMPLETE",)
    assert route_state == ("COMPLETE",)


@pytest.mark.parametrize(
    ("stale_repository", "stale_ref", "new_head"),
    [
        ("example/consumer", "review", "d"),
        ("example/provider", "main", "e"),
    ],
)
def test_routed_success_receipt_cannot_complete_after_live_subject_moves(
    tmp_path: Path,
    stale_repository: str,
    stale_ref: str,
    new_head: str,
):
    db = tmp_path / "queue-worker-stale-live-subject.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )

    route_store = SqliteWorkerRouteStore(db)
    delivery = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-delivery",
        now=3.1,
        ttl=30.0,
        worker_registry_digest="3" * 64,
    )
    assert delivery is not None
    route_store.record_receipt(
        route_id=routed.route_id,
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        holder="worker-delivery",
        expected_fencing_token=delivery.fencing_token,
        receipt_class="SUCCEEDED",
        receipt_sha256="8" * 64,
        now=3.2,
    )
    route_store.close()

    transport.heads[(stale_repository, stale_ref)] = new_head * 40
    with pytest.raises(ValueError, match="currentness is stale"):
        reconcile_queue_item(
            state_db=db,
            snapshot_id=routed.snapshot_id,
            frontier_fingerprint_value=routed.frontier_fingerprint,
            expected_fencing_token=routed.fencing_token,
            resolution="CONFIRM_COMPLETE",
            evidence_sha256="9" * 64,
            reconciler="test-reconciler",
            transport=transport,
            clock=lambda: 4.0,
        )



@pytest.mark.parametrize(
    ("replay_policy", "retry_allowed"),
    [
        ("RECONCILE_REQUIRED", True),
        ("NEVER", False),
    ],
)
def test_ambiguous_worker_delivery_respects_replay_policy(
    tmp_path: Path,
    replay_policy: str,
    retry_allowed: bool,
):
    db = tmp_path / f"queue-worker-{replay_policy.lower()}.sqlite3"
    transport = FakeTransport(
        {
            ("example/provider", "main"): "a" * 40,
            ("example/consumer", "review"): "c" * 40,
        }
    )
    projects = _rereview_projects()
    workers = (_read_only_worker_fixture(replay_policy=replay_policy),)
    dependency = (_rereview_dependency(),)

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=dependency,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )
    routed = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=db,
        holder="route-holder",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 3.0,
    )
    assert routed.queue_state == "ROUTED"

    route_store = SqliteWorkerRouteStore(db)
    delivery = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-delivery",
        now=3.1,
        ttl=1.0,
        worker_registry_digest="3" * 64,
    )
    assert delivery is not None
    reclaimed = route_store.claim_next(
        worker_id="reviewer",
        invocation_route=InvocationRoute.OPENAI_AGENT_API,
        workers=workers,
        holder="worker-two",
        now=5.0,
        ttl=10.0,
        worker_registry_digest="3" * 64,
    )
    assert reclaimed is None
    state = route_store.connection.execute(
        "SELECT state FROM worker_route_outbox WHERE route_id = ?",
        (routed.route_id,),
    ).fetchone()
    assert state == ("OUTCOME_UNKNOWN",)
    route_store.close()

    if retry_allowed:
        result = reconcile_queue_item(
            state_db=db,
            snapshot_id=routed.snapshot_id,
            frontier_fingerprint_value=routed.frontier_fingerprint,
            expected_fencing_token=routed.fencing_token,
            resolution="RELEASE_RETRY_READ_ONLY",
            evidence_sha256="7" * 64,
            reconciler="test-reconciler",
            clock=lambda: 6.0,
        )
        assert result.final_state == "FAILED_RETRYABLE"
        assert result.attempt_generation == 2
    else:
        with pytest.raises(ValueError, match="not qualified"):
            reconcile_queue_item(
                state_db=db,
                snapshot_id=routed.snapshot_id,
                frontier_fingerprint_value=routed.frontier_fingerprint,
                expected_fencing_token=routed.fencing_token,
                resolution="RELEASE_RETRY_READ_ONLY",
                evidence_sha256="7" * 64,
                reconciler="test-reconciler",
                clock=lambda: 6.0,
            )
