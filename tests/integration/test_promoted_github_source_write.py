from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from runner.cli import main
from runner.execution_promotion import (
    execute_promoted,
    promote_claimed_to_running,
    sign_evidence,
)
from runner.github_backend import (
    GitHubFileState,
    GitHubOutcomeUnknown,
    GitHubPreconditionFailed,
)
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.promoted_github import (
    PromotedGitHubSourceWriteBackend,
    source_write_request_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
PROJECTS = ROOT / "registry" / "projects.yaml"
REVIEW_KEY = b"review-source-write-key"
EXECUTION_KEY = b"execution-source-write-key"
EFFECT_KEY = b"effect-source-write-key"


def _git_blob_sha(content: str) -> str:
    raw = content.encode("utf-8")
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw
    ).hexdigest()


class FakeSourceWriteTransport:
    def __init__(self, repository, ref, head):
        self.repository = repository
        self.ref = ref
        self.refs = {(repository, ref): head}
        self.files = {}
        self.mutations = []
        self.read_count = 0
        self.race_on_read = None
        self.race_head = "f" * 40
        self.unknown_after_write = False

    def read_ref(self, repository, ref):
        self.read_count += 1
        if self.race_on_read == self.read_count:
            self.refs[(repository, ref)] = self.race_head
        return self.refs[(repository, ref)]

    def read_file(self, repository, path, ref):
        return self.files.get((repository, path, ref))

    def create_branch(self, repository, branch, sha):
        raise AssertionError("SOURCE_WRITE adapter cannot create branches")

    def put_file_exact_head(
        self,
        repository,
        path,
        branch,
        content,
        message,
        *,
        expected_head,
        expected_blob_sha=None,
    ):
        if self.read_ref(repository, branch) != expected_head:
            raise GitHubPreconditionFailed("exact head mismatch")
        current = self.files.get((repository, path, branch))
        if current is None:
            if expected_blob_sha is not None:
                raise GitHubPreconditionFailed("expected blob missing")
        else:
            if expected_blob_sha is None:
                raise GitHubPreconditionFailed("existing file requires blob sha")
            if current.sha != expected_blob_sha:
                raise GitHubPreconditionFailed("blob mismatch")

        self.mutations.append(
            (
                "put_file",
                repository,
                path,
                branch,
                expected_blob_sha,
            )
        )
        blob_sha = _git_blob_sha(content)
        commit_sha = hashlib.sha1(
            (
                expected_head
                + path
                + content
                + message
            ).encode("utf-8")
        ).hexdigest()
        self.files[(repository, path, branch)] = GitHubFileState(
            sha=blob_sha,
            content=content,
        )
        self.refs[(repository, branch)] = commit_sha
        if self.unknown_after_write:
            raise GitHubOutcomeUnknown("simulated ambiguous write outcome")

        if self.read_ref(repository, branch) != commit_sha:
            raise RuntimeError("readback head mismatch")
        readback = self.read_file(repository, path, branch)
        if (
            readback is None
            or readback.sha != blob_sha
            or readback.content != content
        ):
            raise RuntimeError("readback file mismatch")
        return commit_sha, blob_sha

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        self.mutations.append(
            (
                "put_file",
                repository,
                path,
                branch,
                expected_blob_sha,
            )
        )
        current = self.files.get((repository, path, branch))
        if expected_blob_sha is not None:
            assert current is not None
            assert current.sha == expected_blob_sha
        blob_sha = _git_blob_sha(content)
        self.files[(repository, path, branch)] = GitHubFileState(
            sha=blob_sha,
            content=content,
        )
        commit_sha = hashlib.sha1(
            (
                self.refs[(repository, branch)]
                + path
                + content
                + message
            ).encode("utf-8")
        ).hexdigest()
        self.refs[(repository, branch)] = commit_sha
        return commit_sha


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


def _claim(tmp_path, *, head="a" * 40, ttl=120.0):
    plan = _write_plan(tmp_path)
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    record = next(item for item in corpus.records if item.id == "project-runner")
    transport = FakeSourceWriteTransport(
        record.repository,
        record.default_branch,
        head,
    )
    claim = claim_bound_plan_subject(
        plan_path=plan,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id="project-runner",
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-source-write",
        lease_ttl=ttl,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )
    return claim, transport


def _request(
    claim,
    *,
    path="docs/promoted.txt",
    content="after\n",
    message="Promoted source write",
    expected_blob_sha=None,
):
    return {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
        "operation": "PUT_FILE",
        "repository": claim.repository,
        "ref": claim.ref,
        "expected_head": claim.exact_head,
        "path": path,
        "content": content,
        "message": message,
        "expected_blob_sha": expected_blob_sha,
    }


def _evidence(claim, request, *, request_digest=None):
    digest = request_digest or source_write_request_sha256(request)
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
            "valid_until": 1100.0,
            "execution_request_sha256": digest,
        },
        REVIEW_KEY,
    )
    execution = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
            "grant_id": "source-write-exec-1",
            "issuer": "test-execution-authority",
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
            "valid_until": 1100.0,
        },
        EXECUTION_KEY,
    )
    effect = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_V1",
            "grant_id": "source-write-effect-1",
            "issuer": "test-effect-authority",
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
            "valid_until": 1100.0,
        },
        EFFECT_KEY,
    )
    return review, execution, effect


def _promote(tmp_path, claim, transport, review, execution, effect):
    return promote_claimed_to_running(
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


def test_promoted_source_write_updates_exact_blob_and_reads_back(tmp_path):
    claim, transport = _claim(tmp_path)
    old = "before\n"
    old_blob = _git_blob_sha(old)
    transport.files[
        (claim.repository, "docs/promoted.txt", claim.ref)
    ] = GitHubFileState(sha=old_blob, content=old)

    request = _request(claim, expected_blob_sha=old_blob)
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is True
    assert result.classification == "SUCCEEDED"
    assert result.work_fingerprint == claim.work_fingerprint
    assert result.evidence == (
        "github:promoted-source-write",
        "github:exact-cas",
        "github:readback-verified",
    )
    assert transport.files[
        (claim.repository, "docs/promoted.txt", claim.ref)
    ].content == "after\n"
    assert transport.mutations == [
        (
            "put_file",
            claim.repository,
            "docs/promoted.txt",
            claim.ref,
            old_blob,
        )
    ]


def test_source_write_review_must_bind_exact_request(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(
        claim,
        request,
        request_digest="0" * 64,
    )

    with pytest.raises(
        ValueError,
        match="review evidence does not bind exact execution request",
    ):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            effect,
        )
    assert transport.mutations == []


def test_source_write_effect_authority_must_bind_exact_request(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(claim, request)
    effect["execution_request_sha256"] = "1" * 64
    effect = sign_evidence(effect, EFFECT_KEY)

    with pytest.raises(
        ValueError,
        match="protected-effect authority does not bind exact claim/fence",
    ):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            effect,
        )
    assert transport.mutations == []



def test_noncanonical_signed_path_is_rejected_without_mutation(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim, path="/docs/promoted.txt")
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is False
    assert result.classification == "INVALID_REQUEST"
    assert transport.mutations == []

def test_source_write_blob_mismatch_refuses_mutation(tmp_path):
    claim, transport = _claim(tmp_path)
    old = "before\n"
    transport.files[
        (claim.repository, "docs/promoted.txt", claim.ref)
    ] = GitHubFileState(sha=_git_blob_sha(old), content=old)
    request = _request(
        claim,
        expected_blob_sha="2" * 40,
    )
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is False
    assert result.classification == "PRECONDITION_FAILED"
    assert transport.mutations == []
    assert transport.files[
        (claim.repository, "docs/promoted.txt", claim.ref)
    ].content == old


def test_head_move_after_gate_read_is_still_blocked_by_adapter_cas(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    # Claim read=1, promotion read=2, execute_promoted freshness read=3.
    # Move the branch on the adapter's own exact-head precondition read.
    transport.race_on_read = 4
    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is False
    assert result.classification == "PRECONDITION_FAILED"
    assert transport.mutations == []



def test_ambiguous_source_write_is_journaled_outcome_unknown_without_retry(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )
    transport.unknown_after_write = True
    backend = PromotedGitHubSourceWriteBackend(transport=transport)

    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=backend,
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is False
    assert result.classification == "OUTCOME_UNKNOWN"
    assert len(transport.mutations) == 1

    with pytest.raises(
        ValueError,
        match="already has a backend result",
    ):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=backend,
            transport=transport,
            clock=lambda: 1002.0,
        )
    assert len(transport.mutations) == 1

def test_tampered_durable_request_refuses_execution_before_mutation(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        row = db.execute(
            """
            SELECT request_json
            FROM execution_promotion_requests
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        ).fetchone()
        payload = json.loads(row[0])
        payload["content"] = "tampered\n"
        db.execute(
            """
            UPDATE execution_promotion_requests
            SET request_json = ?
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                json.dumps(payload, sort_keys=True),
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        )

    with pytest.raises(
        ValueError,
        match="durable execution request digest mismatch",
    ):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=PromotedGitHubSourceWriteBackend(transport=transport),
            transport=transport,
            clock=lambda: 1001.0,
        )
    assert transport.mutations == []


def test_adapter_rejects_request_target_divergence_without_mutation(tmp_path):
    claim, transport = _claim(tmp_path)
    request = _request(claim)
    request["repository"] = "thebrazenbeard/not-the-claim"
    digest = source_write_request_sha256(request)
    review, execution, effect = _evidence(
        claim,
        request,
        request_digest=digest,
    )
    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
        clock=lambda: 1001.0,
    )
    assert result.succeeded is False
    assert result.classification == "PROMOTION_BINDING_MISMATCH"
    assert transport.mutations == []


def test_cli_executes_only_previously_promoted_write(tmp_path, monkeypatch, capsys):
    from runner import cli as cli_module

    claim, transport = _claim(tmp_path)
    request = _request(claim)
    review, execution, effect = _evidence(claim, request)
    receipt = _promote(tmp_path, claim, transport, review, execution, effect)
    monkeypatch.setenv("PROJECT_RUNNER_GITHUB_TOKEN", "fixture-only-token")
    monkeypatch.setattr(cli_module, "GitHubRestTransport", lambda *, token: transport)
    monkeypatch.setattr(
        cli_module, "execute_promoted",
        lambda **kwargs: execute_promoted(**kwargs, clock=lambda: 1001.0),
    )
    argv = [
        "github-source-write-execute",
        "--state-db", str(tmp_path / "operator.sqlite3"),
        "--lineage-id", receipt.lineage_id,
        "--work-fingerprint", receipt.work_fingerprint,
        "--fencing-token", str(receipt.fencing_token),
    ]
    assert main(argv) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "SUCCEEDED"
    assert output["backend_executed"] is True
    assert len(transport.mutations) == 1

    with pytest.raises(ValueError, match="already has a backend result"):
        main(argv)
    assert len(transport.mutations) == 1


def test_cli_refuses_source_write_without_durable_promotion(tmp_path, monkeypatch):
    claim, transport = _claim(tmp_path)
    monkeypatch.setenv("PROJECT_RUNNER_GITHUB_TOKEN", "fixture-only-token")
    with pytest.raises(ValueError, match="not durable"):
        main([
            "github-source-write-execute",
            "--state-db", str(tmp_path / "operator.sqlite3"),
            "--lineage-id", claim.lineage_id,
            "--work-fingerprint", claim.work_fingerprint,
            "--fencing-token", str(claim.fencing_token),
        ])
    assert transport.mutations == []
