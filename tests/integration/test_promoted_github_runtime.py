from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from runner.backends import BackendResult
from runner.cli import main
from runner.execution_promotion import (
    promote_claimed_to_running,
    sign_evidence,
)
from runner.github_backend import GitHubFileState
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.promoted_github import source_write_request_sha256
from runner.promoted_github_runtime import (
    finalize_github_source_write_effect_confirmed,
    qualify_github_source_write_runtime,
    reconcile_github_source_write_outcome_unknown,
)


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
PROJECTS = ROOT / "registry" / "projects.yaml"
REVIEW_KEY = b"runtime-review-key"
EXECUTION_KEY = b"runtime-execution-key"
EFFECT_KEY = b"runtime-effect-key"


def _git_blob_sha(content: str) -> str:
    raw = content.encode("utf-8")
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw
    ).hexdigest()


class ReadOnlyQualificationTransport:
    def __init__(self, *, schema_ok=True, move_on_reread=False):
        self.repository = "thebrazenbeard/project-runner"
        self.ref = "main"
        self.head = "a" * 40
        self.tree = "b" * 40
        self.schema_ok = schema_ok
        self.move_on_reread = move_on_reread
        self.calls = []

    def read_ref(self, repository, ref):
        self.calls.append(("read_ref", repository, ref))
        if self.move_on_reread and len(
            [item for item in self.calls if item[0] == "read_ref"]
        ) > 1:
            return "f" * 40
        return self.head

    def read_commit_tree(self, repository, commit_sha):
        self.calls.append(("read_commit_tree", repository, commit_sha))
        assert commit_sha == self.head
        return self.tree

    def inspect_update_refs_schema(self):
        self.calls.append(("inspect_update_refs_schema",))
        fields = ("name", "beforeOid", "afterOid", "force")
        if not self.schema_ok:
            fields = ("name", "afterOid", "force")
        return {
            "mutation_fields": ("updateRefs",),
            "update_refs_input_fields": ("repositoryId", "refUpdates"),
            "ref_update_fields": fields,
        }

    def put_file_exact_head(self, *args, **kwargs):
        raise AssertionError("runtime qualification must remain read-only")


class ReconcileTransport:
    def __init__(self, repository, ref, head):
        self.repository = repository
        self.ref = ref
        self.head = head
        self.files = {}
        self.reads = []
        self.writes = []
        self.ref_sequence = None

    def read_ref(self, repository, ref):
        self.reads.append(("read_ref", repository, ref))
        if self.ref_sequence:
            return self.ref_sequence.pop(0)
        return self.head

    def read_file(self, repository, path, ref):
        self.reads.append(("read_file", repository, path, ref))
        return self.files.get((repository, path, ref))

    def create_branch(self, *args, **kwargs):
        self.writes.append(("create_branch", args, kwargs))
        raise AssertionError("reconciliation must not create branches")

    def put_file(self, *args, **kwargs):
        self.writes.append(("put_file", args, kwargs))
        raise AssertionError("reconciliation must not write files")

    def put_file_exact_head(self, *args, **kwargs):
        self.writes.append(("put_file_exact_head", args, kwargs))
        raise AssertionError("reconciliation must not publish refs")


def _write_plan(tmp_path):
    stdout = StringIO()
    with redirect_stdout(stdout):
        assert main(
            [
                "portfolio-wave-plan",
                "--max-parallel",
                "50",
                "--max-per-identity",
                "50",
                "--max-per-family",
                "50",
            ]
        ) == 0
    payload = json.loads(stdout.getvalue())
    assert any(
        item["subject_id"] == "project-runner"
        for item in payload["selected"]
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _claim_and_promote(tmp_path):
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    record = next(item for item in corpus.records if item.id == "project-runner")
    transport = ReconcileTransport(
        record.repository,
        record.default_branch,
        "a" * 40,
    )
    claim = claim_bound_plan_subject(
        plan_path=_write_plan(tmp_path),
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id="project-runner",
        state_db=tmp_path / "operator.sqlite3",
        holder="runtime-reconciler",
        lease_ttl=300.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )
    request = {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
        "operation": "PUT_FILE",
        "repository": claim.repository,
        "ref": claim.ref,
        "expected_head": claim.exact_head,
        "path": "docs/runtime-reconcile.txt",
        "content": "candidate\n",
        "message": "Candidate source write",
        "expected_blob_sha": None,
    }
    digest = source_write_request_sha256(request)
    review = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_REVIEW_V1",
            "subject_id": claim.subject_id,
            "repository": claim.repository,
            "ref": claim.ref,
            "exact_head": claim.exact_head,
            "plan_sha256": claim.plan_sha256,
            "work_fingerprint": claim.work_fingerprint,
            "reviewer_identity": "REZON",
            "review_gate": "EXACT_HEAD_REVIEW",
            "review_state": "EXECUTION_PROMOTION_REVIEWED",
            "reviewed_at": 990.0,
            "valid_until": 1200.0,
            "execution_request_sha256": digest,
        },
        REVIEW_KEY,
    )
    execution = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
            "grant_id": "runtime-exec",
            "issuer": "runtime-test",
            "subject_id": claim.subject_id,
            "repository": claim.repository,
            "ref": claim.ref,
            "exact_head": claim.exact_head,
            "lineage_id": claim.lineage_id,
            "work_fingerprint": claim.work_fingerprint,
            "fencing_token": claim.fencing_token,
            "operation": "EXECUTE_FRONTIER",
            "effect_class": "SOURCE_WRITE",
            "execution_request": request,
            "execution_authorized": True,
            "issued_at": 995.0,
            "valid_until": 1200.0,
        },
        EXECUTION_KEY,
    )
    effect = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_V1",
            "grant_id": "runtime-effect",
            "issuer": "runtime-test",
            "subject_id": claim.subject_id,
            "repository": claim.repository,
            "ref": claim.ref,
            "exact_head": claim.exact_head,
            "lineage_id": claim.lineage_id,
            "work_fingerprint": claim.work_fingerprint,
            "fencing_token": claim.fencing_token,
            "effect_class": "SOURCE_WRITE",
            "execution_request_sha256": digest,
            "protected_effects_authorized": True,
            "issued_at": 996.0,
            "valid_until": 1200.0,
        },
        EFFECT_KEY,
    )
    receipt = promote_claimed_to_running(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        holder=claim.holder,
        review_document=review,
        execution_grant_document=execution,
        effect_grant_document=effect,
        review_key=REVIEW_KEY,
        execution_authority_key=EXECUTION_KEY,
        effect_authority_key=EFFECT_KEY,
        transport=transport,
        clock=lambda: 1000.0,
    )
    candidate_commit = "c" * 40
    candidate_blob = _git_blob_sha(request["content"])

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        result = BackendResult(
            work_fingerprint=claim.work_fingerprint,
            succeeded=False,
            outputs=(candidate_commit, candidate_blob),
            evidence=("github:outcome_unknown",),
            classification="OUTCOME_UNKNOWN",
        )
    from runner.durable_dispatch import SqliteDispatchAdmissionStore

    store = SqliteDispatchAdmissionStore(tmp_path / "operator.sqlite3")
    store.record_result(
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        result=result,
        recorded_at=1001.0,
    )
    store.close()

    transport.reads.clear()
    return claim, receipt, request, candidate_commit, candidate_blob, transport


def _work_state(db_path, claim):
    with sqlite3.connect(db_path) as db:
        return db.execute(
            """
            SELECT status, generation
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (claim.lineage_id, claim.work_fingerprint),
        ).fetchone()


def test_runtime_qualification_is_read_only_and_passes_required_schema():
    transport = ReadOnlyQualificationTransport(schema_ok=True)
    result = qualify_github_source_write_runtime(
        repository=transport.repository,
        ref=transport.ref,
        transport=transport,
    )

    assert result.status == "PASS"
    assert result.exact_head == transport.head
    assert result.tree_sha == transport.tree
    assert result.write_exercised is False
    assert [item[0] for item in transport.calls] == [
        "read_ref",
        "read_commit_tree",
        "inspect_update_refs_schema",
        "read_ref",
    ]


def test_runtime_qualification_fails_closed_when_ref_moves_during_probe():
    transport = ReadOnlyQualificationTransport(
        schema_ok=True,
        move_on_reread=True,
    )
    result = qualify_github_source_write_runtime(
        repository=transport.repository,
        ref=transport.ref,
        transport=transport,
    )
    assert result.status == "FAIL"
    assert result.stable_snapshot is False
    assert result.write_exercised is False


def test_runtime_qualification_fails_closed_without_before_oid():
    transport = ReadOnlyQualificationTransport(schema_ok=False)
    result = qualify_github_source_write_runtime(
        repository=transport.repository,
        ref=transport.ref,
        transport=transport,
    )
    assert result.status == "FAIL"
    assert result.before_oid_available is False
    assert result.write_exercised is False


def test_unknown_reconciliation_confirms_published_candidate(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    transport.head = candidate_commit
    transport.files[
        (claim.repository, request["path"], candidate_commit)
    ] = GitHubFileState(
        sha=candidate_blob,
        content=request["content"],
    )

    result = reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )

    assert result.outcome == "EFFECT_CONFIRMED"
    assert result.work_generation == 3
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    assert transport.writes == []


def test_pre_write_current_state_does_not_prove_no_effect(tmp_path):
    claim, _receipt, request, _commit, _blob, transport = (
        _claim_and_promote(tmp_path)
    )
    transport.head = claim.exact_head
    assert transport.files.get(
        (claim.repository, request["path"], claim.exact_head)
    ) is None

    result = reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )

    assert result.outcome == "INDETERMINATE"
    assert "cannot prove" in result.reason
    assert result.work_generation == 3
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        lease = db.execute(
            """
            SELECT holder, fencing_token, completed
            FROM leases
            WHERE work_fingerprint = ?
            """,
            (claim.work_fingerprint,),
        ).fetchone()
    assert lease == (
        claim.holder,
        claim.fencing_token,
        0,
    )
    assert transport.writes == []


def test_unknown_reconciliation_is_indeterminate_when_ref_moves_mid_snapshot(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    transport.ref_sequence = [candidate_commit, "d" * 40]
    transport.files[
        (claim.repository, request["path"], candidate_commit)
    ] = GitHubFileState(
        sha=candidate_blob,
        content=request["content"],
    )

    result = reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )

    assert result.outcome == "INDETERMINATE"
    assert "moved during reconciliation snapshot" in result.reason
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    assert transport.writes == []


def test_unknown_reconciliation_remains_indeterminate_on_third_state(tmp_path):
    (
        claim,
        _receipt,
        request,
        _candidate_commit,
        _candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    transport.head = "d" * 40
    transport.files[
        (claim.repository, request["path"], transport.head)
    ] = GitHubFileState(
        sha="e" * 40,
        content="someone else\n",
    )

    result = reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )

    assert result.outcome == "INDETERMINATE"
    assert result.work_generation == 3
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    assert transport.writes == []


def test_reconciliation_refuses_non_unknown_recorded_result(tmp_path):
    claim, _receipt, _request, _commit, _blob, transport = (
        _claim_and_promote(tmp_path)
    )
    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        db.execute(
            """
            UPDATE execution_attempts
            SET result_json = NULL, result_sha256 = NULL, result_recorded_at = NULL
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        )
    from runner.durable_dispatch import SqliteDispatchAdmissionStore

    store = SqliteDispatchAdmissionStore(tmp_path / "operator.sqlite3")
    store.record_result(
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        result=BackendResult(
            work_fingerprint=claim.work_fingerprint,
            succeeded=False,
            outputs=(),
            evidence=("github:precondition_failed",),
            classification="PRECONDITION_FAILED",
        ),
        recorded_at=1001.0,
    )
    store.close()

    with pytest.raises(
        ValueError,
        match="requires recorded OUTCOME_UNKNOWN",
    ):
        reconcile_github_source_write_outcome_unknown(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            reconciler="runtime-reconciler",
            clock=lambda: 1002.0,
        )


def _confirm_effect(tmp_path, claim, request, candidate_commit, candidate_blob, transport):
    transport.head = candidate_commit
    transport.files[
        (claim.repository, request["path"], candidate_commit)
    ] = GitHubFileState(
        sha=candidate_blob,
        content=request["content"],
    )
    return reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )


def test_effect_confirmed_finalization_completes_without_backend_replay(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    reconciliation = _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    assert reconciliation.outcome == "EFFECT_CONFIRMED"

    times = iter((1003.0, 1004.0, 1005.0))
    result = finalize_github_source_write_effect_confirmed(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        clock=lambda: next(times),
    )

    assert result.status == "COMPLETE"
    assert result.backend_replayed is False
    assert result.finalization_replayed is False
    assert result.verified_at == 1005.0
    assert result.candidate_commit_sha == candidate_commit
    assert result.candidate_blob_sha == candidate_blob
    assert result.work_generation == 5
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("COMPLETE", 5)
    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        lease = db.execute(
            """
            SELECT holder, fencing_token, completed
            FROM leases
            WHERE work_fingerprint = ?
            """,
            (claim.work_fingerprint,),
        ).fetchone()
        verification = db.execute(
            """
            SELECT status, reason
            FROM execution_verifications
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        ).fetchone()
    assert lease == (claim.holder, claim.fencing_token, 1)
    assert verification[0] == "COMPLETE"
    assert "no backend replay" in verification[1]
    assert transport.writes == []


def test_completed_effect_finalization_receipt_replays_without_backend_or_git_read(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    times = iter((1003.0, 1004.0, 1005.0))
    first = finalize_github_source_write_effect_confirmed(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        clock=lambda: next(times),
    )
    reads_after_first = tuple(transport.reads)

    transport.head = "d" * 40
    replayed = finalize_github_source_write_effect_confirmed(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        clock=lambda: (_ for _ in ()).throw(
            AssertionError("terminal receipt replay must not sample runtime time")
        ),
    )

    assert replayed.status == "COMPLETE"
    assert replayed.work_generation == first.work_generation == 5
    assert replayed.verified_at == first.verified_at == 1005.0
    assert replayed.reconciliation_sha256 == first.reconciliation_sha256
    assert replayed.backend_replayed is False
    assert replayed.finalization_replayed is True
    assert tuple(transport.reads) == reads_after_first
    assert transport.writes == []


def test_effect_finalization_requires_conclusive_reconciliation(tmp_path):
    claim, _receipt, _request, _commit, _blob, transport = (
        _claim_and_promote(tmp_path)
    )

    with pytest.raises(
        ValueError,
        match="requires conclusive EFFECT_CONFIRMED reconciliation",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    assert transport.writes == []


def test_effect_finalization_refuses_indeterminate_reconciliation(tmp_path):
    claim, _receipt, request, _commit, _blob, transport = (
        _claim_and_promote(tmp_path)
    )
    transport.head = claim.exact_head
    reconciliation = reconcile_github_source_write_outcome_unknown(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        reconciler="runtime-reconciler",
        clock=lambda: 1002.0,
    )
    assert reconciliation.outcome == "INDETERMINATE"

    with pytest.raises(
        ValueError,
        match="requires conclusive EFFECT_CONFIRMED reconciliation",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert transport.writes == []


def test_effect_finalization_refuses_candidate_head_movement(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    transport.head = "d" * 40

    with pytest.raises(
        ValueError,
        match="candidate commit is no longer current",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("RUNNING", 3)
    assert transport.writes == []


def test_effect_finalization_refuses_candidate_blob_mismatch(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    transport.files[
        (claim.repository, request["path"], candidate_commit)
    ] = GitHubFileState(
        sha="e" * 40,
        content=request["content"],
    )

    with pytest.raises(
        ValueError,
        match="candidate blob mismatch",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert transport.writes == []


def test_effect_finalization_refuses_content_mismatch_even_with_blob_label(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    transport.files[
        (claim.repository, request["path"], candidate_commit)
    ] = GitHubFileState(
        sha=candidate_blob,
        content="tampered\n",
    )

    with pytest.raises(
        ValueError,
        match="candidate content mismatch",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert transport.writes == []


def test_effect_finalization_stops_at_verifying_if_candidate_moves_after_checkpoint(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    transport.ref_sequence = [
        candidate_commit,
        candidate_commit,
        "d" * 40,
    ]

    times = iter((1003.0, 1004.0))
    with pytest.raises(
        ValueError,
        match="candidate moved after VERIFYING",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: next(times),
        )

    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("VERIFYING", 4)
    assert transport.writes == []


def test_effect_finalization_resumes_idempotently_from_verifying(tmp_path):
    (
        claim,
        receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )

    from runner.durable_dispatch import SqliteDispatchAdmissionStore
    from runner.leases import Lease

    store = SqliteDispatchAdmissionStore(tmp_path / "operator.sqlite3")
    lease_row = store.connection.execute(
        """
        SELECT holder, fencing_token, expires_at
        FROM leases
        WHERE work_fingerprint = ?
        """,
        (claim.work_fingerprint,),
    ).fetchone()
    lease = Lease(
        work_fingerprint=claim.work_fingerprint,
        holder=str(lease_row[0]),
        fencing_token=int(lease_row[1]),
        expires_at=float(lease_row[2]),
    )
    generation = store.begin_verification(
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        expected_work_generation=receipt.promoted_work_generation,
        lease=lease,
        started_at=1003.0,
    )
    store.close()
    assert generation == 4
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("VERIFYING", 4)

    times = iter((1004.0, 1005.0, 1006.0))
    result = finalize_github_source_write_effect_confirmed(
        state_db=tmp_path / "operator.sqlite3",
        lineage_id=claim.lineage_id,
        work_fingerprint_value=claim.work_fingerprint,
        fencing_token=claim.fencing_token,
        transport=transport,
        clock=lambda: next(times),
    )

    assert result.status == "COMPLETE"
    assert result.work_generation == 5
    assert result.backend_replayed is False
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("COMPLETE", 5)
    assert transport.writes == []


def test_effect_finalization_refuses_fence_expired_before_readback(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )

    with pytest.raises(
        ValueError,
        match="fence has expired",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1400.0,
        )
    assert transport.writes == []


def test_effect_finalization_refuses_fence_expiring_during_readback(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )

    times = iter((1200.0, 1201.0, 1400.0))
    with pytest.raises(
        ValueError,
        match="expired during verification",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: next(times),
        )
    assert _work_state(
        tmp_path / "operator.sqlite3",
        claim,
    ) == ("VERIFYING", 4)
    assert transport.writes == []


def test_effect_finalization_refuses_tampered_reconciliation_receipt(tmp_path):
    (
        claim,
        _receipt,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    ) = _claim_and_promote(tmp_path)
    _confirm_effect(
        tmp_path,
        claim,
        request,
        candidate_commit,
        candidate_blob,
        transport,
    )
    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        db.execute(
            """
            UPDATE execution_reconciliations
            SET reason = 'tampered'
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        )

    with pytest.raises(
        ValueError,
        match="reconciliation journal digest mismatch",
    ):
        finalize_github_source_write_effect_confirmed(
            state_db=tmp_path / "operator.sqlite3",
            lineage_id=claim.lineage_id,
            work_fingerprint_value=claim.work_fingerprint,
            fencing_token=claim.fencing_token,
            transport=transport,
            clock=lambda: 1003.0,
        )
    assert transport.writes == []
