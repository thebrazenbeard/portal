from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from runner.dedup import frontier_fingerprint
from runner.github_backend import GitHubRestTransport
from runner.models import Frontier, Observation, ProjectDefinition
from runner.portfolio import SqlitePortfolioStore
from runner.reference_worker import (
    REFERENCE_WORKER_ID,
    REFERENCE_WORKER_ROUTE,
    run_reference_read_worker_once,
)
from runner.registry import load_worker_snapshot
from runner.worker_routing import (
    SqliteWorkerRouteStore,
    resolve_read_only_worker_route,
)


ROOT = Path(__file__).resolve().parents[1]


def _frontier(repository: str, ref: str, head: str) -> Frontier:
    return Frontier.from_mapping(
        {
            "id": "reference-worker-live-proof",
            "project": "project-runner",
            "subject": {
                "repository": repository,
                "ref": ref,
                "commit": head,
            },
            "work_type": "REREVIEW",
            "reason": "live reference worker proof",
            "dependencies": ["reference-worker-live-proof"],
            "required_capabilities": ["analyze"],
            "collision_keys": ["project:project-runner"],
            "cost_class": "TRIVIAL",
            "priority_inputs": {
                "fanout": 1,
                "blocked_downstream": 0,
                "staleness_risk": 0,
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


def main() -> None:
    repository = os.environ["PR_REPOSITORY"]
    ref = os.environ["PR_REF"]
    expected_head = os.environ["PR_EXPECTED_HEAD"]
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")

    worker_snapshot = load_worker_snapshot(ROOT / "registry" / "workers.yaml")
    worker = next(
        (
            item
            for item in worker_snapshot.workers
            if item.id == REFERENCE_WORKER_ID
        ),
        None,
    )
    if worker is None:
        raise RuntimeError("reference worker is absent from registry")
    if worker.lifecycle.value != "EXECUTABLE":
        raise RuntimeError("reference worker is not EXECUTABLE")
    if worker.routes.get(REFERENCE_WORKER_ROUTE) is None:
        raise RuntimeError("reference worker pull route is absent")
    if worker.routes[REFERENCE_WORKER_ROUTE].value != "VERIFIED":
        raise RuntimeError("reference worker pull route is not VERIFIED")
    if worker.reconstruction is None:
        raise RuntimeError("reference worker lacks reconstruction evidence")

    reconstruction_repository = worker.reconstruction["repository"]
    reconstruction_path = worker.reconstruction["path"]
    reconstruction_commit = worker.reconstruction["commit"]
    reconstruction_transport = GitHubRestTransport(token=token)
    reconstructed = reconstruction_transport.read_file(
        reconstruction_repository,
        reconstruction_path,
        reconstruction_commit,
    )
    if reconstructed is None:
        raise RuntimeError("reference worker reconstruction file is missing")
    local_worker_path = ROOT / reconstruction_path
    if not local_worker_path.is_file():
        raise RuntimeError("reference worker local implementation is missing")
    local_worker_content = local_worker_path.read_text(encoding="utf-8")
    if reconstructed.content != local_worker_content:
        raise RuntimeError(
            "reference worker reconstruction bytes differ from exact workflow subject"
        )
    reconstruction_sha256 = hashlib.sha256(
        reconstructed.content.encode("utf-8")
    ).hexdigest()

    frontier = _frontier(repository, ref, expected_head)
    project = ProjectDefinition.from_mapping(
        {
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": [repository],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "project-runner",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": repository,
                    "ref": ref,
                    "worker_id": REFERENCE_WORKER_ID,
                    "worker_route": REFERENCE_WORKER_ROUTE.value,
                }
            ],
        }
    )
    route = resolve_read_only_worker_route(
        project=project,
        frontier=frontier,
        workers=worker_snapshot.workers,
    )
    if route is None:
        raise RuntimeError("reference worker route did not resolve")

    with tempfile.TemporaryDirectory(prefix="project-runner-reference-proof-") as tmp:
        state_db = Path(tmp) / "state.sqlite3"

        portfolio = SqlitePortfolioStore(state_db)
        snapshot_id = portfolio.commit_cycle(
            registry_digest="0" * 64,
            dependency_digest="1" * 64,
            worker_registry_digest=worker_snapshot.sha256,
            snapshot_digest="2" * 64,
            observed_at=1.0,
            baseline=True,
            observations=(
                Observation.from_mapping(
                    {
                        "target": "project-runner",
                        "evidence_class": "AUTHORITATIVE",
                        "subject": {
                            "repository": repository,
                            "ref": ref,
                            "commit": expected_head,
                        },
                        "observed_value": expected_head,
                        "observed_at": "workflow",
                        "observer": "reference-worker-live-proof",
                    }
                ),
            ),
            changed_count=0,
            ranked_frontiers=(),
            expected_previous_snapshot_id=None,
            expected_previous_dependency_snapshot_id=None,
        )
        portfolio.close()

        store = SqliteWorkerRouteStore(state_db)
        envelope = store.enqueue(
            snapshot_id=snapshot_id,
            frontier_fingerprint=frontier_fingerprint(frontier),
            queue_fencing_token=1,
            worker_registry_digest=worker_snapshot.sha256,
            route=route,
            target_repository=repository,
            target_ref=ref,
            target_head=expected_head,
            frontier_json=_frontier_json(frontier),
            now=2.0,
        )
        store.close()

        result = run_reference_read_worker_once(
            state_db=state_db,
            workers=worker_snapshot.workers,
            worker_registry_digest=worker_snapshot.sha256,
            holder="github-actions-reference-proof",
            lease_ttl=300.0,
            token=token,
        )
        if not result.claimed:
            raise RuntimeError("reference worker did not claim proof envelope")
        if result.route_id != envelope.route_id:
            raise RuntimeError("reference worker claimed wrong route")
        if result.receipt_class != "SUCCEEDED":
            raise RuntimeError(
                f"reference worker proof did not succeed: {result.receipt_class}"
            )

        store = SqliteWorkerRouteStore(state_db)
        try:
            receipt = store.connection.execute(
                """
                SELECT state, receipt_class, receipt_sha256
                FROM worker_route_outbox
                WHERE route_id = ?
                """,
                (envelope.route_id,),
            ).fetchone()
        finally:
            store.close()
        if receipt is None:
            raise RuntimeError("reference worker receipt is missing")
        if tuple(receipt[:2]) != ("RECEIPT_RECORDED", "SUCCEEDED"):
            raise RuntimeError("reference worker receipt is not terminal success")
        if str(receipt[2]) != result.receipt_sha256:
            raise RuntimeError("reference worker receipt digest mismatch")

    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "worker_id": REFERENCE_WORKER_ID,
                "route": REFERENCE_WORKER_ROUTE.value,
                "repository": repository,
                "ref": ref,
                "expected_head": expected_head,
                "receipt_class": "SUCCEEDED",
                "worker_registry_digest": worker_snapshot.sha256,
                "reconstruction_repository": reconstruction_repository,
                "reconstruction_path": reconstruction_path,
                "reconstruction_commit": reconstruction_commit,
                "reconstruction_blob_sha": reconstructed.sha,
                "reconstruction_sha256": reconstruction_sha256,
                "reconstruction_exact_bytes": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
