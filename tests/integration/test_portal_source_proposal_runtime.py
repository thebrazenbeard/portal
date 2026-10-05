from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import time

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from portal.models import ExecutionNode
from portal.wave_runtime import (
    PortalWaveStore,
    prepare_portal_wave,
)
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_runtime import run_wave_proposal_workers_once
from runner.models import ProjectDefinition
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_wave_scheduler import WaveExecutionBudget


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"


class FakeReadOnlyTransport:
    def __init__(self, heads):
        self.heads = dict(heads)

    def read_ref(self, repository, ref):
        return self.heads[(repository, ref)]

    def read_file(self, repository, path, ref):
        return None

    def create_branch(self, repository, branch, sha):
        raise AssertionError("proposal worker must not mutate source")

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        raise AssertionError("proposal worker must not mutate source")


def _seed(tmp_path: Path):
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
    projects = tmp_path / "projects.live.yaml"
    write_project_registry(projects, snapshot)

    db = tmp_path / "portal.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    now = time.time()
    prepared = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects,
        state_db=db,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
            max_per_lane=1,
        ),
        run_id="proposal-run",
        holder="vera",
        lease_ttl=600.0,
        token=None,
        transport=transport,
        clock=lambda: now,
    )
    assert len(prepared.packets) == 1
    return db, transport, prepared.packets[0]


def _worker(path: Path) -> None:
    path.write_text(
        """
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()

packet = json.loads(Path(args.portal_packet).read_text(encoding="utf-8"))
assert packet["execution_authorized"] is False
assert packet["advisory_only"] is True
assert packet["source_mutation_authorized"] is False

proposal = {
    "schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
    "repository": packet["repository"],
    "source_ref": packet["source_ref"],
    "expected_head": packet["exact_head"],
    "message": "Implement " + packet["subject_id"],
    "files": [
        {
            "path": "portal-proposal.txt",
            "content": "proposal for " + packet["subject_id"] + "\\n",
            "expected_blob_sha": None,
        }
    ],
}
proposal_path = Path(args.portal_receipt).parent / "proposal.json"
proposal_path.write_text(
    json.dumps(proposal, sort_keys=True),
    encoding="utf-8",
)
digest = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "PROPOSED_SOURCE_TREE",
            "reason": "exact source proposal ready",
            "artifacts": [
                {
                    "kind": "SOURCE_TREE_PROPOSAL",
                    "relative_path": "proposal.json",
                    "sha256": digest,
                }
            ],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )


def test_advisory_worker_proposal_becomes_awaiting_promotion(
    tmp_path: Path,
) -> None:
    db, transport, packet = _seed(tmp_path)
    worker = tmp_path / "worker.py"
    _worker(worker)

    result = run_wave_proposal_workers_once(
        state_db=db,
        run_id="proposal-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal-proposal",
        delivery_lease_ttl=30.0,
        token=None,
        transport=transport,
        clock=time.time,
    )

    assert result.claimed == 1
    assert result.awaiting_promotion == 1
    assert result.no_work == 0
    assert result.slots[0].state == "AWAITING_PROMOTION"

    store = PortalWaveStore(db)
    try:
        summary = store.summary("proposal-run")
        proposal = store.load_source_proposal(
            run_id="proposal-run",
            subject_id=packet.subject_id,
        )
    finally:
        store.close()

    assert summary["delivery_states"] == {"AWAITING_PROMOTION": 1}
    assert summary["proposals"] == 1
    assert proposal["sha256"]
    assert proposal["execution_request"]["schema"] == (
        "PROJECT_RUNNER_GITHUB_SOURCE_TREE_WRITE_V1"
    )
    assert proposal["execution_request"]["expected_head"] == packet.exact_head

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            """
            SELECT status
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (packet.lineage_id, packet.work_fingerprint),
        ).fetchone()
    assert row == ("CLAIMED",)


def test_advisory_proposal_pass_does_not_reclaim_awaiting_promotion(
    tmp_path: Path,
) -> None:
    db, transport, _packet = _seed(tmp_path)
    worker = tmp_path / "worker.py"
    _worker(worker)
    kwargs = dict(
        state_db=db,
        run_id="proposal-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal-proposal",
        delivery_lease_ttl=30.0,
        token=None,
        transport=transport,
        clock=time.time,
    )

    first = run_wave_proposal_workers_once(**kwargs)
    second = run_wave_proposal_workers_once(**kwargs)

    assert first.awaiting_promotion == 1
    assert second.claimed == 0
    assert second.no_work == 1
