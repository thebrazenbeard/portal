from __future__ import annotations

from pathlib import Path
import sys
import time

import pytest

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from portal.models import ExecutionNode
from portal.wave_runtime import (
    PortalWaveStore,
    prepare_portal_wave,
    promote_portal_wave_packet,
)
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_runtime import run_wave_workers_once
from runner.execution_promotion import (
    NO_PROTECTED_EFFECT,
    sign_evidence,
)
from runner.models import ProjectDefinition
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_wave_scheduler import WaveExecutionBudget


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
REVIEW_KEY = b"portal-auto-worker-review"
EXECUTION_KEY = b"portal-auto-worker-execution"


class FakeReadOnlyTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.reads = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.reads.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository: str, path: str, ref: str):
        return None

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("automatic worker runtime must not mutate source")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ):
        raise AssertionError("automatic worker runtime must not mutate source")


def _seed(db: Path, *, capacity: int = 2):
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    repos = []
    curated = []
    heads = {}
    for record in corpus.records:
        repos.append(
            RepositoryInventoryItem(
                name=record.repository.split("/", 1)[1],
                full_name=record.repository,
                private=False,
                archived=record.archived,
                default_branch=record.default_branch,
            )
        )
        curated.append(
            ProjectDefinition.from_mapping(
                {
                    "id": record.id,
                    "name": record.name,
                    "visibility": "public",
                    "repositories": [record.repository],
                    "capabilities": ["read", "analyze", "propose"],
                    "assignment_scope": "NONE",
                    "review_scope": "NONE",
                    "family_id": record.family_id,
                    "scheduling_state": (
                        "ARCHIVED" if record.archived else "SCHEDULABLE"
                    ),
                }
            )
        )
        heads[(record.repository, record.default_branch)] = "a" * 40

    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=tuple(repos),
        curated_projects=tuple(curated),
    )
    projects = db.parent / "projects.live.yaml"
    write_project_registry(projects, snapshot)

    transport = FakeReadOnlyTransport(heads)
    now = time.time()
    prepared = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects,
        state_db=db,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=capacity),),
        budget=WaveExecutionBudget(
            max_parallel=capacity,
            max_per_identity=capacity,
            max_per_family=capacity,
            max_per_lane=capacity,
        ),
        run_id="auto-run",
        holder="vera",
        lease_ttl=600.0,
        token=None,
        transport=transport,
        clock=lambda: now,
    )
    assert len(prepared.packets) == capacity

    for packet in prepared.packets:
        review = sign_evidence(
            {
                "schema": "PROJECT_RUNNER_EXECUTION_REVIEW_V1",
                "subject_id": packet.subject_id,
                "repository": packet.repository,
                "ref": packet.ref,
                "exact_head": packet.exact_head,
                "plan_sha256": packet.plan_sha256,
                "work_fingerprint": packet.work_fingerprint,
                "reviewer_identity": packet.reviewer_identities[0],
                "review_gate": packet.review_gate,
                "review_state": "EXECUTION_PROMOTION_REVIEWED",
                "reviewed_at": now,
                "valid_until": now + 600.0,
                "execution_request_sha256": None,
            },
            REVIEW_KEY,
        )
        execution = sign_evidence(
            {
                "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
                "grant_id": f"auto:{packet.subject_id}",
                "issuer": "portal-test-authority",
                "subject_id": packet.subject_id,
                "repository": packet.repository,
                "ref": packet.ref,
                "exact_head": packet.exact_head,
                "lineage_id": packet.lineage_id,
                "work_fingerprint": packet.work_fingerprint,
                "fencing_token": packet.fencing_token,
                "operation": packet.action,
                "effect_class": NO_PROTECTED_EFFECT,
                "execution_request": None,
                "execution_authorized": True,
                "issued_at": now,
                "valid_until": now + 600.0,
            },
            EXECUTION_KEY,
        )
        promote_portal_wave_packet(
            state_db=db,
            run_id="auto-run",
            subject_id=packet.subject_id,
            review_document=review,
            execution_grant_document=execution,
            effect_grant_document=None,
            review_key=REVIEW_KEY,
            execution_authority_key=EXECUTION_KEY,
            effect_authority_key=None,
            token=None,
            transport=transport,
            clock=lambda: now + 1.0,
        )

    return transport, prepared.packets


def _worker(path: Path) -> None:
    path.write_text(
        """
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()
packet = json.loads(Path(args.portal_packet).read_text(encoding="utf-8"))
assert packet["execution_authorized"] is True
assert packet["execution_effect_class"] == "NO_PROTECTED_EFFECT"
assert packet["source_mutation_authorized"] is False
Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "SUCCEEDED_NO_EFFECT",
            "reason": "analysis complete for " + packet["subject_id"],
            "artifacts": [],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )


def test_automatic_worker_pass_fills_node_capacity_and_verifies(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    transport, packets = _seed(db)
    worker = tmp_path / "worker.py"
    _worker(worker)

    result = run_wave_workers_once(
        state_db=db,
        run_id="auto-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=30.0,
        verifier="vera-review",
        token=None,
        transport=transport,
    )

    assert result.claimed == 2
    assert result.verified_complete == 2
    assert result.no_work == 0
    assert {slot.subject_id for slot in result.slots} == {
        packet.subject_id for packet in packets
    }
    assert {slot.state for slot in result.slots} == {"VERIFIED_COMPLETE"}

    store = PortalWaveStore(db)
    try:
        summary = store.summary("auto-run")
    finally:
        store.close()
    assert summary["delivery_states"] == {"VERIFIED_COMPLETE": 2}


def test_automatic_worker_pass_is_idle_after_packets_are_terminal(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    transport, _packets = _seed(db)
    worker = tmp_path / "worker.py"
    _worker(worker)
    kwargs = dict(
        state_db=db,
        run_id="auto-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=30.0,
        verifier="vera-review",
        token=None,
        transport=transport,
    )

    first = run_wave_workers_once(**kwargs)
    second = run_wave_workers_once(**kwargs)

    assert first.verified_complete == 2
    assert second.claimed == 0
    assert second.no_work == 2


def test_automatic_worker_pass_requires_backend_for_every_enabled_node(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    transport, _packets = _seed(db)

    with pytest.raises(ValueError, match="missing worker backend for enabled node"):
        run_wave_workers_once(
            state_db=db,
            run_id="auto-run",
            nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
            backends={},
            workspace_root=tmp_path / "workers",
            holder_prefix="portal",
            delivery_lease_ttl=30.0,
            verifier="vera-review",
            token=None,
            transport=transport,
        )


def test_thirteen_source_only_process_slots_are_actually_exercised(
    tmp_path: Path,
) -> None:
    """Exercise 13 real subprocess receipts, not a 13-row advisory plan."""
    db = tmp_path / "portal.sqlite3"
    transport, packets = _seed(db, capacity=13)
    worker = tmp_path / "worker.py"
    _worker(worker)
    result = run_wave_workers_once(
        state_db=db,
        run_id="auto-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=13),),
        backends={"alpha": ProcessWorkerSpec(
            command=(sys.executable, str(worker)),
            timeout_seconds=30.0,
            pass_env=(),
        )},
        workspace_root=tmp_path / "workers",
        holder_prefix="source-only-fixture",
        delivery_lease_ttl=60.0,
        verifier="vera-review",
        token=None,
        transport=transport,
    )
    assert result.claimed == result.verified_complete == 13
    assert result.failed_or_unknown == 0
    assert result.no_work == 0
    assert len({slot.holder for slot in result.slots}) == 13
    assert {slot.subject_id for slot in result.slots} == {
        packet.subject_id for packet in packets
    }
    assert {slot.state for slot in result.slots} == {"VERIFIED_COMPLETE"}
    store = PortalWaveStore(db)
    try:
        assert store.summary("auto-run")["delivery_states"] == {
            "VERIFIED_COMPLETE": 13
        }
    finally:
        store.close()
