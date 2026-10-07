from __future__ import annotations

from pathlib import Path

import pytest

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from portal.live_portfolio import refresh_live_local_portfolio
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

def test_unsupported_queued_workstream_does_not_consume_packet_capacity(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    state_db = tmp_path / "portal-unsupported-workstream.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    wave = __import__(
        "runner.portfolio_advancement",
        fromlist=["load_advancement_wave"],
    ).load_advancement_wave(WAVE)
    excluded = tuple(
        (item.subject_kind, item.subject_id)
        for item in wave.items
        if item.subject_kind == "repository"
        and item.subject_id != "repairtracker"
    )

    result = prepare_portal_wave(
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
        run_id="wave-workstream-filter",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        excluded_subjects=excluded,
        transport=transport,
        clock=lambda: 100.0,
    )

    assert [packet.subject_id for packet in result.packets] == ["repairtracker"]
    assert result.claimed == 1
    assert result.held == 0


def test_active_workstream_still_consumes_budget_while_queued_workstreams_are_skipped(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    state_db = tmp_path / "portal-active-workstream.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    wave = __import__(
        "runner.portfolio_advancement",
        fromlist=["load_advancement_wave"],
    ).load_advancement_wave(WAVE)
    excluded = tuple(
        (item.subject_kind, item.subject_id)
        for item in wave.items
        if item.subject_kind == "repository"
        and item.subject_id != "repairtracker"
    )

    result = prepare_portal_wave(
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=projects_path,
        state_db=state_db,
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=2,
            max_per_family=2,
            max_per_lane=2,
        ),
        run_id="wave-active-workstream",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        excluded_subjects=excluded,
        active_subjects=(("workstream", "nature-of-existence"),),
        transport=transport,
        clock=lambda: 100.0,
    )

    assert [packet.subject_id for packet in result.packets] == ["repairtracker"]
    assert result.claimed == 1
    assert result.held == 0



def test_prepare_wave_accepts_private_repository_from_explicit_local_overlay(
    tmp_path: Path,
):
    repositories = (
        RepositoryInventoryItem(
            name="project-runner",
            full_name="thebrazenbeard/project-runner",
            private=False,
            archived=False,
            default_branch="main",
        ),
        RepositoryInventoryItem(
            name="private-live",
            full_name="thebrazenbeard/private-live",
            private=True,
            archived=False,
            default_branch="main",
        ),
    )
    live = refresh_live_local_portfolio(
        baseline_corpus_path=CORPUS,
        baseline_wave_path=WAVE,
        repositories=repositories,
        observed_at="2026-10-06T19:30:00Z",
        output_dir=tmp_path / "live-local",
    )
    corpus = load_portfolio_corpus(live.corpus_path)
    curated = tuple(
        ProjectDefinition.from_mapping(
            {
                "id": record.id,
                "name": record.name,
                "visibility": record.visibility,
                "repositories": [record.repository],
                "capabilities": ["read", "analyze", "propose"],
                "assignment_scope": "NONE",
                "review_scope": "NONE",
                "family_id": record.family_id,
                "scheduling_state": "SCHEDULABLE",
            }
        )
        for record in corpus.records
        if not record.archived
    )
    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=repositories,
        curated_projects=curated,
    )
    projects_path = tmp_path / "projects.live.yaml"
    write_project_registry(projects_path, snapshot)
    heads = {
        (record.repository, record.default_branch): "d" * 40
        for record in corpus.records
        if not record.archived
    }
    transport = FakeReadOnlyTransport(heads)

    result = prepare_portal_wave(
        wave_path=live.wave_path,
        corpus_path=live.corpus_path,
        projects_path=projects_path,
        state_db=tmp_path / "private-portal.sqlite3",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
            max_per_lane=1,
        ),
        run_id="private-live-wave",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        excluded_subjects=(("repository", "project-runner"),),
        public_safe=False,
        transport=transport,
        clock=lambda: 100.0,
    )

    assert [packet.subject_id for packet in result.packets] == ["private-live"]
    assert result.packets[0].effect_ceiling == "NO_EFFECT"
    assert result.packets[0].repository == "thebrazenbeard/private-live"
    assert transport.mutations == []


def test_prepare_wave_local_mode_rejects_incomplete_count_only_corpus(
    tmp_path: Path,
):
    projects_path, heads = _operator_registry(tmp_path)
    transport = FakeReadOnlyTransport(heads)

    with pytest.raises(
        ValueError,
        match="complete portfolio corpus must enumerate every repository",
    ):
        prepare_portal_wave(
            wave_path=WAVE,
            corpus_path=CORPUS,
            projects_path=projects_path,
            state_db=tmp_path / "incomplete-private.sqlite3",
            nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
            budget=WaveExecutionBudget(
                max_parallel=1,
                max_per_identity=1,
                max_per_family=1,
                max_per_lane=1,
            ),
            run_id="incomplete-private-wave",
            holder="vera",
            lease_ttl=60.0,
            token=None,
            public_safe=False,
            transport=transport,
            clock=lambda: 100.0,
        )
