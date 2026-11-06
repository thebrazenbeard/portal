"""Three-process, installed-wheel, public read-only reference-worker proof.

Run prepare, consume, verify as separate Python invocations against a disposable
SQLite file. Never writes to GitHub, starts host services, or grants authority.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import tempfile

import portal
import runner
from portal._runner.dedup import frontier_fingerprint
from portal._runner.models import Frontier, Observation, ProjectDefinition
from portal._runner.portfolio import SqlitePortfolioStore
from portal._runner.reference_worker import (
    REFERENCE_WORKER_ID,
    REFERENCE_WORKER_ROUTE,
    run_reference_read_worker_once,
)
from portal._runner.registry import load_worker_snapshot
from portal._runner.worker_routing import SqliteWorkerRouteStore, resolve_read_only_worker_route


TARGET_REPO = "thebrazenbeard/project-runner"
TARGET_REF = "main"


def workers():
    path = files("portal").joinpath("_data", "registry", "workers.yaml")
    return load_worker_snapshot(Path(str(path)))


def frontier(head: str):
    return Frontier.from_mapping({
        "id": "installed-wheel-restart-proof", "project": "project-runner",
        "subject": {"repository": TARGET_REPO, "ref": TARGET_REF, "commit": head},
        "work_type": "REREVIEW", "reason": "public read-only installed worker proof",
        "dependencies": ["installed-wheel-restart-proof"],
        "required_capabilities": ["analyze"], "collision_keys": ["project:project-runner"],
        "cost_class": "TRIVIAL", "priority_inputs": {
            "fanout": 1, "blocked_downstream": 0, "staleness_risk": 0,
            "failure_severity": 0, "declared_priority": 0, "cost": 1,
            "authority_available": 1, "scheduling_eligible": 1, "executable_now": 1,
        }, "status": "READY",
    })


def frontier_payload(item):
    return json.dumps({
        "id": item.id, "project": item.project,
        "subject": {"repository": item.subject.repository, "ref": item.subject.ref,
                    "commit": item.subject.commit, "path": item.subject.path,
                    "digest": item.subject.digest},
        "work_type": item.work_type, "reason": item.reason,
        "dependencies": list(item.dependencies),
        "required_capabilities": list(item.required_capabilities),
        "collision_keys": list(item.collision_keys),
        "cost_class": item.cost_class.value,
        "priority_inputs": dict(item.priority_inputs),
        "status": item.status.value,
    }, sort_keys=True)


def prepare(db: Path, head: str):
    if db.exists():
        raise RuntimeError("refusing to overwrite an existing qualification state DB")
    registry = workers()
    item = frontier(head)
    project = ProjectDefinition.from_mapping({
        "id": "project-runner", "name": "Project Runner", "visibility": "public",
        "repositories": [TARGET_REPO], "capabilities": ["read", "analyze"],
        "assignment_scope": "NONE", "review_scope": "NONE",
        "family_id": "project-runner", "scheduling_state": "SCHEDULABLE",
        "execution_targets": [{
            "work_type": "REREVIEW", "repository": TARGET_REPO, "ref": TARGET_REF,
            "worker_id": REFERENCE_WORKER_ID,
            "worker_route": REFERENCE_WORKER_ROUTE.value,
        }],
    })
    route = resolve_read_only_worker_route(
        project=project, frontier=item, workers=registry.workers,
    )
    if route is None:
        raise RuntimeError("read-only worker route did not resolve")
    store = SqlitePortfolioStore(db)
    try:
        snapshot_id = store.commit_cycle(
            registry_digest="0" * 64, dependency_digest="1" * 64,
            worker_registry_digest=registry.sha256,
            snapshot_digest="2" * 64, observed_at=1.0, baseline=True,
            observations=(Observation.from_mapping({
                "target": "project-runner", "evidence_class": "AUTHORITATIVE",
                "subject": {"repository": TARGET_REPO, "ref": TARGET_REF, "commit": head},
                "observed_value": head, "observed_at": "qualification",
                "observer": "installed-wheel-restart-proof",
            }),),
            changed_count=0, ranked_frontiers=(),
            expected_previous_snapshot_id=None,
            expected_previous_dependency_snapshot_id=None,
        )
    finally:
        store.close()
    outbox = SqliteWorkerRouteStore(db)
    try:
        envelope = outbox.enqueue(
            snapshot_id=snapshot_id, frontier_fingerprint=frontier_fingerprint(item),
            queue_fencing_token=1, worker_registry_digest=registry.sha256,
            route=route, target_repository=TARGET_REPO, target_ref=TARGET_REF,
            target_head=head, frontier_json=frontier_payload(item), now=2.0,
        )
    finally:
        outbox.close()
    return {"phase": "PREPARED", "route_id": envelope.route_id,
            "target_head": head, "registry_sha256": registry.sha256}


class _OfflineReadRef:
    """Deterministic synthetic transport used ONLY by explicit fixture mode."""

    def __init__(self, observed_head: str):
        self.observed_head = observed_head

    def read_ref(self, repository: str, ref: str) -> str:
        if (repository, ref) != (TARGET_REPO, TARGET_REF):
            raise RuntimeError("offline fixture requested an unexpected target")
        return self.observed_head


def consume(db: Path, fixture_observed_head: str | None = None):
    registry = workers()
    if fixture_observed_head is not None and (len(fixture_observed_head) != 40
                                             or any(x not in "0123456789abcdef" for x in fixture_observed_head)):
        raise ValueError("offline fixture requires exact lowercase SHA-1 shape")
    result = run_reference_read_worker_once(
        state_db=db, workers=registry.workers,
        worker_registry_digest=registry.sha256,
        holder="installed-wheel-test-process", token=None,
        transport=(_OfflineReadRef(fixture_observed_head)
                   if fixture_observed_head is not None else None),
    )
    if not result.claimed or result.receipt_class != "SUCCEEDED":
        raise RuntimeError(f"reference worker failed: {result}")
    return {"phase": "RECEIPT_RECORDED", "route_id": result.route_id,
            "fencing_token": result.delivery_fencing_token,
            "receipt_class": result.receipt_class,
            "transport_class": ("SYNTHETIC_OFFLINE" if fixture_observed_head is not None
                                else "PUBLIC_GITHUB_READ_ONLY"),
            "receipt_sha256": result.receipt_sha256}


def verify(db: Path):
    registry = workers()
    store = SqliteWorkerRouteStore(db)
    try:
        rows = store.connection.execute(
            "SELECT route_id, state, receipt_class, receipt_sha256, "
            "delivery_fencing_token FROM worker_route_outbox"
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("exactly one durable route expected")
        route_id, state, receipt_class, receipt_sha256, fencing_token = rows[0]
        if (state, receipt_class) != ("RECEIPT_RECORDED", "SUCCEEDED"):
            raise RuntimeError("durable receipt is not terminal success")
        if not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64:
            raise RuntimeError("receipt digest missing")
    finally:
        store.close()
    replay = run_reference_read_worker_once(
        state_db=db, workers=registry.workers,
        worker_registry_digest=registry.sha256,
        holder="installed-wheel-replay-check", token=None,
    )
    if replay.claimed:
        raise RuntimeError("terminal route was claimed again")
    return {"phase": "REOPENED_AND_VERIFIED", "route_id": route_id,
            "receipt_class": receipt_class,
            "receipt_sha256": receipt_sha256,
            "delivery_fencing_token": fencing_token,
            "replay_claimed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "consume", "verify"), required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--target-head", default=None)
    parser.add_argument("--fixture-observed-head", default=None,
                        help="Explicit deterministic offline transport; NEVER a live qualification")
    args = parser.parse_args()
    safe_tmp = Path(tempfile.gettempdir()).resolve()
    if (not args.db.resolve().is_relative_to(safe_tmp)
            or args.db.name != "qualification.sqlite3"):
        raise ValueError("qualification database must be named qualification.sqlite3 under OS temp")
    if not str(portal.__file__).find("site-packages") >= 0:
        raise RuntimeError("Portal is not imported from an installed wheel")
    if not str(runner.__file__).find("site-packages") >= 0:
        raise RuntimeError("Runner is not imported from an installed wheel")
    if args.phase == "prepare":
        if args.fixture_observed_head:
            raise ValueError("fixture observed head only applies to consume phase")
        if (not args.target_head or len(args.target_head) != 40
                or any(x not in "0123456789abcdef" for x in args.target_head)):
            raise ValueError("prepare requires exact lowercase 40-character target SHA")
        result = prepare(args.db, args.target_head)
    elif args.phase == "consume":
        result = consume(args.db, fixture_observed_head=args.fixture_observed_head)
    else:
        if args.fixture_observed_head:
            raise ValueError("fixture observed head only applies to consume phase")
        result = verify(args.db)
    result["installed_portal_file_sha256"] = hashlib.sha256(
        Path(portal.__file__).read_bytes()).hexdigest()
    result["installed_runner_file_sha256"] = hashlib.sha256(
        Path(runner.__file__).read_bytes()).hexdigest()
    result["evidence_class"] = "ISOLATED_INSTALLED_WHEELS_PUBLIC_READ_ONLY"
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
