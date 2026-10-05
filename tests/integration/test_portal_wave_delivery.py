from __future__ import annotations

from pathlib import Path
import sqlite3

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
    verify_portal_wave_delivery,
)
from runner.execution_promotion import (
    NO_PROTECTED_EFFECT,
    promote_claimed_to_running,
    sign_evidence,
)
from runner.models import ProjectDefinition
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_wave_scheduler import WaveExecutionBudget


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
REVIEW_KEY = b"portal-wave-review-test-key"
EXECUTION_KEY = b"portal-wave-execution-test-key"


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
        raise AssertionError("delivery verification must not mutate repositories")

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
        raise AssertionError("delivery verification must not mutate repositories")


def _prepared(tmp_path: Path):
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
    projects_path = tmp_path / "projects.live.yaml"
    write_project_registry(projects_path, snapshot)
    state_db = tmp_path / "portal.sqlite3"
    transport = FakeReadOnlyTransport(heads)
    prepared = prepare_portal_wave(
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
        run_id="delivery-run",
        holder="vera",
        lease_ttl=60.0,
        token=None,
        transport=transport,
        clock=lambda: 100.0,
    )
    assert len(prepared.packets) == 1
    return state_db, projects_path, transport, prepared.packets[0]


def _promote_no_effect(
    state_db: Path,
    transport: FakeReadOnlyTransport,
    packet,
) -> None:
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
            "reviewed_at": 101.0,
            "valid_until": 150.0,
            "execution_request_sha256": None,
        },
        REVIEW_KEY,
    )
    execution = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
            "grant_id": f"portal-test:{packet.subject_id}",
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
            "issued_at": 102.0,
            "valid_until": 150.0,
        },
        EXECUTION_KEY,
    )
    promote_claimed_to_running(
        state_db=state_db,
        lineage_id=packet.lineage_id,
        work_fingerprint_value=packet.work_fingerprint,
        fencing_token=packet.fencing_token,
        holder="vera",
        review_document=review,
        execution_grant_document=execution,
        effect_grant_document=None,
        review_key=REVIEW_KEY,
        execution_authority_key=EXECUTION_KEY,
        effect_authority_key=None,
        transport=transport,
        clock=lambda: 105.0,
    )


def _prepared_promoted(tmp_path: Path):
    state_db, projects_path, transport, packet = _prepared(tmp_path)
    _promote_no_effect(state_db, transport, packet)
    return state_db, projects_path, transport, packet


def test_unpromoted_packet_is_not_deliverable(tmp_path: Path) -> None:
    state_db, _projects_path, _transport, _packet = _prepared(tmp_path)
    store = PortalWaveStore(state_db)
    try:
        with pytest.raises(
            ValueError,
            match="Project Runner execution promotion",
        ):
            store.claim_delivery(
                run_id="delivery-run",
                node_id="alpha",
                holder="worker-alpha",
                now=110.0,
                ttl=30.0,
            )
    finally:
        store.close()


def test_delivery_claim_is_fenced_and_payload_preserves_authority_boundary(
    tmp_path: Path,
) -> None:
    state_db, _projects_path, _transport, packet = _prepared_promoted(tmp_path)
    store = PortalWaveStore(state_db)
    try:
        claim = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-alpha",
            now=110.0,
            ttl=30.0,
        )
        assert claim is not None
        assert claim.subject_id == packet.subject_id
        assert claim.node_id == "alpha"
        assert claim.fencing_token == 1
        assert claim.lease_expires_at == 140.0
        assert claim.payload["repository"] == packet.repository
        assert claim.payload["exact_head"] == packet.exact_head
        assert claim.payload["frontier"] == packet.frontier
        assert claim.payload["effect_ceiling"] == "SOURCE_ONLY"
        assert claim.payload["execution_authorized"] is True
        assert claim.payload["execution_effect_class"] == "NO_PROTECTED_EFFECT"
        assert claim.payload["protected_effects_authorized"] is False
        assert claim.payload["source_mutation_authorized"] is False
        assert claim.payload["target_ref_mutation_authorized"] is False

        blocked = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-beta",
            now=120.0,
            ttl=30.0,
        )
        assert blocked is None
    finally:
        store.close()


def test_expired_delivery_becomes_outcome_unknown_instead_of_replaying(
    tmp_path: Path,
) -> None:
    state_db, _projects_path, _transport, _packet = _prepared_promoted(tmp_path)
    store = PortalWaveStore(state_db)
    try:
        first = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-alpha",
            now=110.0,
            ttl=10.0,
        )
        assert first is not None

        second = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-beta",
            now=121.0,
            ttl=10.0,
        )
        assert second is None
        summary = store.summary("delivery-run")
        assert summary["delivery_states"] == {"OUTCOME_UNKNOWN": 1}
    finally:
        store.close()


def test_worker_delivery_cannot_report_source_change_without_promoted_execution(
    tmp_path: Path,
) -> None:
    state_db, _projects_path, _transport, packet = _prepared_promoted(tmp_path)
    store = PortalWaveStore(state_db)
    try:
        claim = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-alpha",
            now=110.0,
            ttl=30.0,
        )
        assert claim is not None

        with pytest.raises(
            ValueError,
            match="source mutation must use Project Runner promoted execution",
        ):
            store.record_delivery_receipt(
                run_id="delivery-run",
                subject_id=packet.subject_id,
                node_id="alpha",
                holder="worker-alpha",
                expected_fencing_token=claim.fencing_token,
                receipt_class="SUCCEEDED_SOURCE_CHANGE",
                result_repository=packet.repository,
                result_ref="work/portal/proposal",
                result_head="b" * 40,
                evidence_sha256="e" * 64,
                reason="worker attempted to report source mutation",
                now=115.0,
            )
    finally:
        store.close()

def test_no_effect_receipt_is_independently_verified_before_completion(
    tmp_path: Path,
) -> None:
    state_db, _projects_path, transport, packet = _prepared_promoted(tmp_path)

    store = PortalWaveStore(state_db)
    try:
        claim = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-alpha",
            now=110.0,
            ttl=30.0,
        )
        assert claim is not None
        receipt = store.record_delivery_receipt(
            run_id="delivery-run",
            subject_id=packet.subject_id,
            node_id="alpha",
            holder="worker-alpha",
            expected_fencing_token=claim.fencing_token,
            receipt_class="SUCCEEDED_NO_EFFECT",
            result_repository=None,
            result_ref=None,
            result_head=None,
            evidence_sha256="e" * 64,
            reason="analysis completed without repository mutation",
            now=115.0,
        )
        assert receipt.state == "RECEIPT_RECORDED"
        assert store.summary("delivery-run")["delivery_states"] == {
            "RECEIPT_RECORDED": 1
        }
    finally:
        store.close()

    verified = verify_portal_wave_delivery(
        state_db=state_db,
        run_id="delivery-run",
        subject_id=packet.subject_id,
        verifier="vera-review",
        token=None,
        transport=transport,
        clock=lambda: 120.0,
    )
    assert verified.state == "VERIFIED_COMPLETE"
    assert verified.result_head is None

    with sqlite3.connect(state_db) as db:
        work = db.execute(
            """
            SELECT status
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (packet.lineage_id, packet.work_fingerprint),
        ).fetchone()
        lease = db.execute(
            """
            SELECT completed
            FROM leases
            WHERE work_fingerprint = ?
            """,
            (packet.work_fingerprint,),
        ).fetchone()
    assert work == ("COMPLETE",)
    assert lease == (1,)

    store = PortalWaveStore(state_db)
    try:
        assert store.summary("delivery-run")["delivery_states"] == {
            "VERIFIED_COMPLETE": 1
        }
    finally:
        store.close()
    assert transport.mutations == []

def test_verification_marks_stale_when_original_exact_subject_moved(
    tmp_path: Path,
) -> None:
    state_db, _projects_path, transport, packet = _prepared_promoted(tmp_path)

    store = PortalWaveStore(state_db)
    try:
        claim = store.claim_delivery(
            run_id="delivery-run",
            node_id="alpha",
            holder="worker-alpha",
            now=110.0,
            ttl=30.0,
        )
        assert claim is not None
        store.record_delivery_receipt(
            run_id="delivery-run",
            subject_id=packet.subject_id,
            node_id="alpha",
            holder="worker-alpha",
            expected_fencing_token=claim.fencing_token,
            receipt_class="SUCCEEDED_NO_EFFECT",
            result_repository=None,
            result_ref=None,
            result_head=None,
            evidence_sha256="e" * 64,
            reason="analysis completed",
            now=115.0,
        )
    finally:
        store.close()

    transport.heads[(packet.repository, packet.ref)] = "c" * 40

    verified = verify_portal_wave_delivery(
        state_db=state_db,
        run_id="delivery-run",
        subject_id=packet.subject_id,
        verifier="vera-review",
        token=None,
        transport=transport,
        clock=lambda: 120.0,
    )
    assert verified.state == "VERIFICATION_STALE"

