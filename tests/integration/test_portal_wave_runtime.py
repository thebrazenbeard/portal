from __future__ import annotations

from pathlib import Path

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from portal.models import ExecutionNode
from portal.wave_runtime import PortalWaveStore, prepare_portal_wave
from runner.models import ProjectDefinition
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_wave_scheduler import WaveExecutionBudget


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"


class FakeReadOnlyTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.reads = []
        self.mutations = []

    def read_ref(self, repository, ref):
        self.reads.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository, path, ref):
        return None

    def create_branch(self, repository, branch, sha):
        self.mutations.append(("create_branch", repository, branch, sha))
        raise AssertionError("wave preparation must not mutate repositories")

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        self.mutations.append(("put_file", repository, path, branch))
        raise AssertionError("wave preparation must not mutate repositories")


def _operator_registry(tmp_path: Path):
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
    path = tmp_path / "projects.live.yaml"
    write_project_registry(path, snapshot)
    return path, heads


def test_prepare_wave_claims_exact_assigned_subjects_into_durable_outbox(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    state_db = tmp_path / "portal.sqlite3"
    transport = FakeReadOnlyTransport(heads)

    result = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects_path,
        state_db=state_db,
        nodes=(
            ExecutionNode(node_id="alpha", max_parallel=1),
            ExecutionNode(node_id="beta", max_parallel=1),
        ),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=2,
            max_per_family=2,
            max_per_lane=2,
        ),
        run_id="wave-run",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )

    assert result.assigned == 2
    assert result.claimed == 2
    assert result.held == 0
    assert len(result.packets) == 2
    assert {packet.node_id for packet in result.packets} == {"alpha", "beta"}
    assert all(packet.state == "CLAIMED" for packet in result.packets)
    assert all(packet.exact_head == "a" * 40 for packet in result.packets)
    assert all(packet.fencing_token == 1 for packet in result.packets)
    assert transport.mutations == []

    store = PortalWaveStore(state_db)
    try:
        summary = store.summary("wave-run")
    finally:
        store.close()

    assert summary["packets"] == 2
    assert summary["states"] == {"CLAIMED": 2}


def test_prepare_wave_is_idempotent_for_same_holder_and_exact_heads(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    state_db = tmp_path / "portal-idempotent.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    kwargs = dict(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects_path,
        state_db=state_db,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
            max_per_lane=1,
        ),
        run_id="wave-run",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )

    first = prepare_portal_wave(**kwargs)
    second = prepare_portal_wave(**kwargs)

    assert first.packets == second.packets
    store = PortalWaveStore(state_db)
    try:
        summary = store.summary("wave-run")
    finally:
        store.close()
    assert summary["packets"] == 1



def test_prepare_wave_excludes_prior_subject_and_refills_next_candidate(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    state_db = tmp_path / "portal-refill.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    budget = WaveExecutionBudget(
        max_parallel=1,
        max_per_identity=1,
        max_per_family=1,
        max_per_lane=1,
    )
    nodes = (ExecutionNode(node_id="alpha", max_parallel=1),)

    first = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects_path,
        state_db=state_db,
        nodes=nodes,
        budget=budget,
        run_id="wave-1",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )
    assert len(first.packets) == 1
    first_subject = first.packets[0].subject_id

    second = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects_path,
        state_db=state_db,
        nodes=nodes,
        budget=budget,
        run_id="wave-2",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        excluded_subjects=(("repository", first_subject),),
        transport=transport,
        clock=lambda: 101.0,
    )

    assert len(second.packets) == 1
    assert second.packets[0].subject_id != first_subject
