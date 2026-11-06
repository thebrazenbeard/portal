"""Three-process, installed-wheel, public read-only reference-worker proof.

Run prepare, consume, verify as separate Python invocations against a disposable
SQLite file. Never writes to GitHub, starts host services, or grants authority.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
from importlib.metadata import distribution
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import tempfile

import portal
import runner
from portal._runner.dedup import frontier_fingerprint
from portal._runner.github_backend import GitHubRestTransport
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
PROOF_SCHEMA = "installed-worker-restart-hmac-v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _witness_paths(db: Path) -> tuple[Path, Path, Path]:
    return (db.with_name("qualification.key"),
            db.with_name("qualification.prepare.json"),
            db.with_name("qualification.consume.json"))


def _write_once(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _signed(payload: dict[str, object], key: bytes) -> bytes:
    return _canonical({"payload": payload,
                       "hmac_sha256": hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()})


def _load_witness(path: Path, key: bytes) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError(f"trusted durable witness missing: {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
        raise RuntimeError(f"witness has invalid schema: {path.name}")
    expected_mac = hmac.new(key, _canonical(data["payload"]), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(data.get("hmac_sha256")), expected_mac):
        raise RuntimeError(f"witness integrity failed: {path.name}")
    if data["payload"].get("schema") != PROOF_SCHEMA:
        raise RuntimeError(f"witness has unknown schema: {path.name}")
    return data["payload"]


_ROUTE_FIELDS = ("route_id", "snapshot_id", "frontier_fingerprint",
                 "queue_fencing_token", "worker_registry_digest",
                 "packet_sensitivity", "worker_id", "invocation_route",
                 "replay_policy", "target_repository", "target_ref", "target_head",
                 "frontier_json", "payload_sha256")


def _route_row(connection):
    rows = connection.execute(
        f"SELECT {', '.join(_ROUTE_FIELDS)}, state, receipt_class, "
        "receipt_sha256, delivery_fencing_token FROM worker_route_outbox"
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("exactly one durable route expected")
    values = dict(zip(_ROUTE_FIELDS + ("state", "receipt_class", "receipt_sha256",
                                        "delivery_fencing_token"), rows[0]))
    return values


def _assert_prepared_route(db: Path, prepared: dict[str, object]):
    store = SqliteWorkerRouteStore(db)
    try:
        route = _route_row(store.connection)
    finally:
        store.close()
    for field in _ROUTE_FIELDS:
        if route[field] != prepared.get(field):
            raise RuntimeError(f"durable route differs from signed prepared witness: {field}")
    return route


def _trusted_prepare(db: Path):
    key_path, prepared_path, _ = _witness_paths(db)
    if not key_path.is_file():
        raise RuntimeError("trusted durable prepared witness key missing")
    key = key_path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("prepared witness key missing or invalid")
    prepared = _load_witness(prepared_path, key)
    _assert_prepared_route(db, prepared)
    return key, prepared


def assert_installed_distinct_wheels() -> None:
    """Reject source-tree imports or a shared/overwritten wheel namespace."""
    expected = (
        ("portal-coordinator", "portal", portal),
        ("project-runner", "runner", runner),
    )
    for distribution_name, module_name, module in expected:
        installed = distribution(distribution_name)
        packaged = {str(p).replace("\\", "/") for p in (installed.files or ())}
        entry = f"{module_name}/__init__.py"
        if entry not in packaged:
            raise RuntimeError(f"{distribution_name} wheel does not own {entry}")
        expected_file = Path(installed.locate_file(entry)).resolve()
        module_file = Path(module.__file__).resolve()
        if expected_file != module_file or "site-packages" not in expected_file.parts:
            raise RuntimeError(f"{module_name} is not imported from its installed wheel")


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
    key_path, prepared_path, consumed_path = _witness_paths(db)
    if any(path.exists() for path in (key_path, prepared_path, consumed_path)):
        raise RuntimeError("refusing to reuse existing qualification witness files")
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
    outbox = SqliteWorkerRouteStore(db)
    try:
        record = _route_row(outbox.connection)
    finally:
        outbox.close()
    if record["state"] != "PENDING" or record["route_id"] != envelope.route_id:
        raise RuntimeError("prepared route must be the single pending route")
    key = secrets.token_bytes(32)
    _write_once(key_path, key)
    witness = {"schema": PROOF_SCHEMA, **{field: record[field] for field in _ROUTE_FIELDS}}
    _write_once(prepared_path, _signed(witness, key))
    return {"phase": "PREPARED", "route_id": envelope.route_id,
            "target_head": head, "registry_sha256": registry.sha256,
            "prepared_witness_sha256": hashlib.sha256(_canonical(witness)).hexdigest()}


class _OfflineReadRef:
    """Deterministic synthetic transport used ONLY by explicit fixture mode."""

    def __init__(self, observed_head: str):
        self.observed_head = observed_head

    def read_ref(self, repository: str, ref: str) -> str:
        if (repository, ref) != (TARGET_REPO, TARGET_REF):
            raise RuntimeError("offline fixture requested an unexpected target")
        return self.observed_head


class _RecordingReadRef:
    """Capture exact read results used by the genuine wheel worker for hashing."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.reads: list[dict[str, str]] = []

    def read_ref(self, repository: str, ref: str) -> str:
        result = self.delegate.read_ref(repository, ref)
        self.reads.append({"repository": repository, "ref": ref, "head": result})
        return result


def consume(db: Path, fixture_observed_head: str | None = None):
    registry = workers()
    key, prepared = _trusted_prepare(db)
    _, _, consumed_path = _witness_paths(db)
    if consumed_path.exists():
        raise RuntimeError("consume witness already exists; refusing replay")
    route = _assert_prepared_route(db, prepared)
    if route["state"] != "PENDING" or route["receipt_sha256"] is not None:
        raise RuntimeError("consume requires exact signed pending route")
    if prepared["worker_registry_digest"] != registry.sha256:
        raise RuntimeError("prepared worker registry differs from installed wheel")
    if fixture_observed_head is not None and (len(fixture_observed_head) != 40
                                             or any(x not in "0123456789abcdef" for x in fixture_observed_head)):
        raise ValueError("offline fixture requires exact lowercase SHA-1 shape")
    transport = _RecordingReadRef(_OfflineReadRef(fixture_observed_head)
                                  if fixture_observed_head is not None
                                  else GitHubRestTransport(token=None))
    result = run_reference_read_worker_once(
        state_db=db, workers=registry.workers,
        worker_registry_digest=registry.sha256,
        holder="installed-wheel-test-process", token=None,
        transport=transport,
    )
    if not result.claimed or result.receipt_class != "SUCCEEDED":
        raise RuntimeError(f"reference worker failed: {result}")
    expected_reads = [{"repository": TARGET_REPO, "ref": TARGET_REF,
                       "head": prepared["target_head"]}] * 2
    if transport.reads != expected_reads:
        raise RuntimeError("reference worker observations did not match the pinned public route")
    evidence = {
        "route_id": prepared["route_id"], "worker_id": REFERENCE_WORKER_ID,
        "invocation_route": REFERENCE_WORKER_ROUTE.value,
        "delivery_fencing_token": result.delivery_fencing_token,
        "provider_expected": prepared["target_head"],
        "provider_observed": transport.reads[0]["head"],
        "target_expected": prepared["target_head"],
        "target_observed": transport.reads[1]["head"],
        "transport_state": "READ_SUCCEEDED", "receipt_class": "SUCCEEDED",
    }
    independently_recomputed = hashlib.sha256(_canonical(evidence)).hexdigest()
    if (result.route_id != prepared["route_id"]
            or not isinstance(result.delivery_fencing_token, int)
            or result.delivery_fencing_token < 1
            or independently_recomputed != result.receipt_sha256):
        raise RuntimeError("installed worker receipt does not match independently captured observations")
    row = _assert_prepared_route(db, prepared)
    if (row["state"], row["receipt_class"], row["receipt_sha256"],
            row["delivery_fencing_token"]) != (
            "RECEIPT_RECORDED", "SUCCEEDED", independently_recomputed,
            result.delivery_fencing_token):
        raise RuntimeError("durable receipt differs from independently recomputed worker evidence")
    witness = {"schema": PROOF_SCHEMA, "prepared_sha256": hashlib.sha256(
        _canonical(prepared)).hexdigest(), "receipt_evidence": evidence,
        "observed_reads": transport.reads, "receipt_sha256": independently_recomputed,
        "transport_class": ("SYNTHETIC_OFFLINE" if fixture_observed_head is not None
                            else "PUBLIC_GITHUB_READ_ONLY")}
    _write_once(consumed_path, _signed(witness, key))
    return {"phase": "RECEIPT_RECORDED", "route_id": result.route_id,
            "fencing_token": result.delivery_fencing_token,
            "receipt_class": result.receipt_class,
            "transport_class": ("SYNTHETIC_OFFLINE" if fixture_observed_head is not None
                                else "PUBLIC_GITHUB_READ_ONLY"),
            "receipt_sha256": result.receipt_sha256}


def verify(db: Path):
    registry = workers()
    key, prepared = _trusted_prepare(db)
    _, _, consumed_path = _witness_paths(db)
    consumed = _load_witness(consumed_path, key)
    if (consumed.get("prepared_sha256") != hashlib.sha256(_canonical(prepared)).hexdigest()
            or consumed.get("transport_class") not in
            ("PUBLIC_GITHUB_READ_ONLY", "SYNTHETIC_OFFLINE")):
        raise RuntimeError("consume witness is not bound to exact prepared route")
    evidence = consumed.get("receipt_evidence")
    observations = consumed.get("observed_reads")
    if not isinstance(evidence, dict) or not isinstance(observations, list):
        raise RuntimeError("consume witness has no independently witnessed evidence")
    expected_reads = [{"repository": TARGET_REPO, "ref": TARGET_REF,
                       "head": prepared["target_head"]}] * 2
    if observations != expected_reads:
        raise RuntimeError("consume witness observations differ from prepared public head")
    expected_evidence = {
        "route_id": prepared["route_id"], "worker_id": REFERENCE_WORKER_ID,
        "invocation_route": REFERENCE_WORKER_ROUTE.value,
        "delivery_fencing_token": evidence.get("delivery_fencing_token"),
        "provider_expected": prepared["target_head"],
        "provider_observed": observations[0]["head"],
        "target_expected": prepared["target_head"],
        "target_observed": observations[1]["head"],
        "transport_state": "READ_SUCCEEDED", "receipt_class": "SUCCEEDED",
    }
    if (evidence != expected_evidence
            or not isinstance(evidence.get("delivery_fencing_token"), int)
            or evidence["delivery_fencing_token"] < 1):
        raise RuntimeError("consume witness receipt content does not match observed reads")
    recomputed_sha = hashlib.sha256(_canonical(evidence)).hexdigest()
    row = _assert_prepared_route(db, prepared)
    route_id = row["route_id"]
    receipt_class = row["receipt_class"]
    receipt_sha256 = row["receipt_sha256"]
    fencing_token = row["delivery_fencing_token"]
    if (row["state"], receipt_class, receipt_sha256, fencing_token) != (
            "RECEIPT_RECORDED", "SUCCEEDED", recomputed_sha,
            evidence["delivery_fencing_token"]):
        raise RuntimeError("durable receipt differs from authenticated consume witness")
    if consumed.get("receipt_sha256") != recomputed_sha:
        raise RuntimeError("consume witness receipt hash is inconsistent with content")
    if prepared["worker_registry_digest"] != registry.sha256:
        raise RuntimeError("installed worker registry changed since prepare")
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
    assert_installed_distinct_wheels()
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
