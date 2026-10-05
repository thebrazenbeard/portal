from __future__ import annotations

from pathlib import Path
import sys

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from portal.ecosystem_runtime import (
    PortalEcosystemStore,
    run_ecosystem_proposal_generations,
)
from portal.models import ExecutionNode
from portal.worker_backend import ProcessWorkerSpec
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

    def create_branch(self, *args, **kwargs):
        raise AssertionError("proposal generation must not mutate source")

    def put_file(self, *args, **kwargs):
        raise AssertionError("proposal generation must not mutate source")


def _registry(tmp_path: Path):
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
    return projects, heads


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
proposal = {
    "schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
    "repository": packet["repository"],
    "source_ref": packet["source_ref"],
    "expected_head": packet["exact_head"],
    "message": "Advance " + packet["subject_id"],
    "files": [
        {
            "path": "portal-advance.txt",
            "content": "advance " + packet["subject_id"] + "\\n",
            "expected_blob_sha": None,
        }
    ],
}
proposal_path = Path(args.portal_receipt).parent / "proposal.json"
proposal_path.write_text(json.dumps(proposal, sort_keys=True), encoding="utf-8")
digest = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "PROPOSED_SOURCE_TREE",
            "reason": "bounded proposal ready",
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


def test_ecosystem_session_refills_unique_repository_subjects_across_generations(
    tmp_path: Path,
) -> None:
    projects, heads = _registry(tmp_path)
    worker = tmp_path / "worker.py"
    _worker(worker)
    db = tmp_path / "portal.sqlite3"

    result = run_ecosystem_proposal_generations(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects,
        state_db=db,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=2,
            max_per_family=2,
            max_per_lane=2,
        ),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        session_id="ecosystem",
        holder="vera",
        lease_ttl=600.0,
        delivery_lease_ttl=30.0,
        holder_prefix="portal",
        max_generations=2,
        token=None,
        transport=FakeReadOnlyTransport(heads),
    )

    assert result.generations == 2
    assert result.admitted == 4
    assert result.awaiting_promotion == 4
    assert result.duplicate_admissions == 0
    assert result.workstreams_remaining == 2

    store = PortalEcosystemStore(db)
    try:
        summary = store.summary("ecosystem")
    finally:
        store.close()
    assert summary["generations"] == 2
    assert summary["admitted_subjects"] == 4
    assert summary["effective_states"] == {"AWAITING_PROMOTION": 4}


def test_ecosystem_session_resume_continues_with_next_subjects(
    tmp_path: Path,
) -> None:
    projects, heads = _registry(tmp_path)
    worker = tmp_path / "worker.py"
    _worker(worker)
    db = tmp_path / "portal.sqlite3"
    common = dict(
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
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        session_id="ecosystem",
        holder="vera",
        lease_ttl=600.0,
        delivery_lease_ttl=30.0,
        holder_prefix="portal",
        max_generations=1,
        token=None,
        transport=FakeReadOnlyTransport(heads),
    )

    first = run_ecosystem_proposal_generations(**common)
    second = run_ecosystem_proposal_generations(**common)

    assert first.admitted == 1
    assert second.admitted == 1

    store = PortalEcosystemStore(db)
    try:
        subjects = store.subjects("ecosystem")
    finally:
        store.close()
    assert len(subjects) == 2
    assert len({item["subject_id"] for item in subjects}) == 2
