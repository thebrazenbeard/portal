from pathlib import Path
import json

from runner.dedup import frontier_fingerprint
from runner.models import (
    Frontier,
    Observation,
    ProjectDefinition,
    WorkerDefinition,
)
from runner.portfolio import SqlitePortfolioStore
from runner.reference_worker import (
    REFERENCE_WORKER_ID,
    REFERENCE_WORKER_ROUTE,
    run_reference_read_worker_once,
)
from runner.worker_routing import (
    SqliteWorkerRouteStore,
    resolve_read_only_worker_route,
)


class FakeTransport:
    def __init__(self, heads):
        self.heads = dict(heads)

    def read_ref(self, repository: str, ref: str) -> str:
        value = self.heads[(repository, ref)]
        if isinstance(value, Exception):
            raise value
        return value

    def read_file(self, repository, path, ref):
        raise AssertionError("reference worker only reads refs")

    def create_branch(self, repository, branch, sha):
        raise AssertionError("reference worker is read-only")

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        raise AssertionError("reference worker is read-only")


def _worker() -> WorkerDefinition:
    return WorkerDefinition.from_mapping(
        {
            "id": REFERENCE_WORKER_ID,
            "name": "Project Runner Reference Read Worker",
            "worker_type": "GITHUB_ACTION",
            "lifecycle": "EXECUTABLE",
            "locators": {
                "workflow": ".github/workflows/test.yml",
                "command": "project-runner run-reference-worker",
            },
            "roles": ["reference-read-worker"],
            "routes": {"RUNNER_ACTION_PULL": "VERIFIED"},
            "route_contracts": {
                "RUNNER_ACTION_PULL": {
                    "effect_class": "READ_ONLY",
                    "replay_policy": "SAFE",
                }
            },
            "reconstruction": {
                "repository": "thebrazenbeard/project-runner",
                "path": "runner/reference_worker.py",
                "commit": "50af885c961d78aef0e5f8a735f699a6108a42af",
            },
        }
    )


def _frontier() -> Frontier:
    return Frontier.from_mapping(
        {
            "id": "reference-rereview",
            "project": "project-runner",
            "subject": {
                "repository": "thebrazenbeard/project-runner",
                "ref": "work/reference",
                "commit": "a" * 40,
            },
            "work_type": "REREVIEW",
            "reason": "reference worker proof",
            "dependencies": ["reference-proof"],
            "required_capabilities": ["analyze"],
            "collision_keys": ["project:project-runner"],
            "cost_class": "SMALL",
            "priority_inputs": {
                "fanout": 1,
                "blocked_downstream": 0,
                "staleness_risk": 1,
                "failure_severity": 0,
                "declared_priority": 0,
                "cost": 1,
                "authority_available": 1,
                "scheduling_eligible": 1,
                "executable_now": 1,
            },
            "status": "READY",
        }
    )


def _frontier_json(frontier: Frontier) -> str:
    return json.dumps(
        {
            "id": frontier.id,
            "project": frontier.project,
            "subject": {
                "repository": frontier.subject.repository,
                "ref": frontier.subject.ref,
                "commit": frontier.subject.commit,
                "path": frontier.subject.path,
                "digest": frontier.subject.digest,
            },
            "work_type": frontier.work_type,
            "reason": frontier.reason,
            "dependencies": list(frontier.dependencies),
            "required_capabilities": list(frontier.required_capabilities),
            "collision_keys": list(frontier.collision_keys),
            "cost_class": frontier.cost_class.value,
            "priority_inputs": dict(frontier.priority_inputs),
            "status": frontier.status.value,
        },
        sort_keys=True,
    )


def _setup_route(tmp_path: Path):
    db = tmp_path / "reference-worker.sqlite3"
    frontier = _frontier()
    worker = _worker()

    portfolio = SqlitePortfolioStore(db)
    snapshot_id = portfolio.commit_cycle(
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        snapshot_digest="4" * 64,
        observed_at=1.0,
        baseline=True,
        observations=(
            Observation.from_mapping(
                {
                    "target": "project-runner",
                    "evidence_class": "AUTHORITATIVE",
                    "subject": {
                        "repository": frontier.subject.repository,
                        "ref": frontier.subject.ref,
                        "commit": frontier.subject.commit,
                    },
                    "observed_value": frontier.subject.commit,
                    "observed_at": "test",
                    "observer": "test",
                }
            ),
        ),
        changed_count=0,
        ranked_frontiers=(),
        expected_previous_snapshot_id=None,
        expected_previous_dependency_snapshot_id=None,
    )
    portfolio.close()

    project = ProjectDefinition.from_mapping(
        {
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": ["thebrazenbeard/project-runner"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "project-runner",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": "thebrazenbeard/project-runner",
                    "ref": "work/reference",
                    "worker_id": REFERENCE_WORKER_ID,
                    "worker_route": REFERENCE_WORKER_ROUTE.value,
                }
            ],
        }
    )
    route = resolve_read_only_worker_route(
        project=project,
        frontier=frontier,
        workers=(worker,),
    )
    assert route is not None

    routes = SqliteWorkerRouteStore(db)
    envelope = routes.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint=frontier_fingerprint(frontier),
        queue_fencing_token=1,
        worker_registry_digest="3" * 64,
        route=route,
        target_repository="thebrazenbeard/project-runner",
        target_ref="work/reference",
        target_head="a" * 40,
        frontier_json=_frontier_json(frontier),
        now=2.0,
    )
    routes.close()
    return db, worker, envelope


def test_reference_worker_records_success_receipt(tmp_path: Path):
    db, worker, envelope = _setup_route(tmp_path)
    result = run_reference_read_worker_once(
        state_db=db,
        workers=(worker,),
        worker_registry_digest="3" * 64,
        holder="reference-worker",
        lease_ttl=60.0,
        transport=FakeTransport(
            {
                ("thebrazenbeard/project-runner", "work/reference"): "a" * 40,
            }
        ),
        clock=iter((3.0, 4.0)).__next__,
    )
    assert result.claimed is True
    assert result.route_id == envelope.route_id
    assert result.receipt_class == "SUCCEEDED"
    assert result.receipt_sha256 is not None

    store = SqliteWorkerRouteStore(db)
    try:
        row = store.connection.execute(
            """
            SELECT state, receipt_class, receipt_sha256
            FROM worker_route_outbox
            WHERE route_id = ?
            """,
            (envelope.route_id,),
        ).fetchone()
    finally:
        store.close()
    assert row == (
        "RECEIPT_RECORDED",
        "SUCCEEDED",
        result.receipt_sha256,
    )


def test_reference_worker_marks_moved_target_superseded(tmp_path: Path):
    db, worker, _envelope = _setup_route(tmp_path)
    result = run_reference_read_worker_once(
        state_db=db,
        workers=(worker,),
        worker_registry_digest="3" * 64,
        holder="reference-worker",
        lease_ttl=60.0,
        transport=FakeTransport(
            {
                ("thebrazenbeard/project-runner", "work/reference"): "b" * 40,
            }
        ),
        clock=iter((3.0, 4.0)).__next__,
    )
    assert result.claimed is True
    assert result.receipt_class == "SUPERSEDED"


def test_reference_worker_records_retryable_transport_failure(tmp_path: Path):
    db, worker, _envelope = _setup_route(tmp_path)
    result = run_reference_read_worker_once(
        state_db=db,
        workers=(worker,),
        worker_registry_digest="3" * 64,
        holder="reference-worker",
        lease_ttl=60.0,
        transport=FakeTransport(
            {
                ("thebrazenbeard/project-runner", "work/reference"):
                    RuntimeError("synthetic transport failure"),
            }
        ),
        clock=iter((3.0, 4.0)).__next__,
    )
    assert result.claimed is True
    assert result.receipt_class == "FAILED_RETRYABLE"


def test_reference_worker_no_work_is_noop(tmp_path: Path):
    db = tmp_path / "empty-reference-worker.sqlite3"
    worker = _worker()
    result = run_reference_read_worker_once(
        state_db=db,
        workers=(worker,),
        worker_registry_digest="3" * 64,
        holder="reference-worker",
        transport=FakeTransport({}),
        clock=lambda: 1.0,
    )
    assert result.claimed is False
    assert result.receipt_class is None



def test_reference_worker_skips_private_packet_and_takes_public_work(
    tmp_path: Path,
):
    db = tmp_path / "reference-worker-private-skip.sqlite3"
    worker = _worker()
    frontier = _frontier()

    portfolio = SqlitePortfolioStore(db)
    snapshot_id = portfolio.commit_cycle(
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        snapshot_digest="4" * 64,
        observed_at=1.0,
        baseline=True,
        observations=(
            Observation.from_mapping(
                {
                    "target": "project-runner",
                    "evidence_class": "AUTHORITATIVE",
                    "subject": {
                        "repository": frontier.subject.repository,
                        "ref": frontier.subject.ref,
                        "commit": frontier.subject.commit,
                    },
                    "observed_value": frontier.subject.commit,
                    "observed_at": "test",
                    "observer": "test",
                }
            ),
        ),
        changed_count=0,
        ranked_frontiers=(),
        expected_previous_snapshot_id=None,
        expected_previous_dependency_snapshot_id=None,
    )
    portfolio.close()

    project = ProjectDefinition.from_mapping(
        {
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": ["thebrazenbeard/project-runner"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "project-runner",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": "thebrazenbeard/project-runner",
                    "ref": "work/reference",
                    "worker_id": REFERENCE_WORKER_ID,
                    "worker_route": REFERENCE_WORKER_ROUTE.value,
                }
            ],
        }
    )
    route = resolve_read_only_worker_route(
        project=project,
        frontier=frontier,
        workers=(worker,),
    )
    assert route is not None

    routes = SqliteWorkerRouteStore(db)
    private_envelope = routes.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="1" * 64,
        queue_fencing_token=1,
        worker_registry_digest="3" * 64,
        route=route,
        target_repository="thebrazenbeard/project-runner",
        target_ref="work/reference",
        target_head="a" * 40,
        frontier_json=_frontier_json(frontier),
        now=2.0,
        packet_sensitivity="PRIVATE",
    )
    public_envelope = routes.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="2" * 64,
        queue_fencing_token=2,
        worker_registry_digest="3" * 64,
        route=route,
        target_repository="thebrazenbeard/project-runner",
        target_ref="work/reference",
        target_head="a" * 40,
        frontier_json=_frontier_json(frontier),
        now=2.1,
        packet_sensitivity="PUBLIC",
    )
    routes.close()

    result = run_reference_read_worker_once(
        state_db=db,
        workers=(worker,),
        worker_registry_digest="3" * 64,
        holder="reference-worker",
        lease_ttl=60.0,
        transport=FakeTransport(
            {
                ("thebrazenbeard/project-runner", "work/reference"): "a" * 40,
            }
        ),
        clock=iter((3.0, 4.0)).__next__,
    )
    assert result.claimed is True
    assert result.route_id == public_envelope.route_id

    routes = SqliteWorkerRouteStore(db)
    try:
        private_state = routes.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (private_envelope.route_id,),
        ).fetchone()
        public_state = routes.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (public_envelope.route_id,),
        ).fetchone()
    finally:
        routes.close()

    assert private_state == ("PENDING",)
    assert public_state == ("RECEIPT_RECORDED",)
