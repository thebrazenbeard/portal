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
    NO_PROTECTED_EFFECT,
    execute_promoted,
    promote_claimed_to_running,
    sign_evidence,
)
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.promoted_github import source_write_request_sha256


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
PROJECTS = ROOT / "registry" / "projects.yaml"
REVIEW_KEY = b"review-test-key"
EXECUTION_KEY = b"execution-authority-test-key"
EFFECT_KEY = b"protected-effect-authority-test-key"


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
        raise AssertionError("promotion gate must not create branches")

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
        raise AssertionError("promotion gate must not put files")


class SpyPromotedBackend:
    def __init__(self):
        self.calls = []

    def execute_promoted(self, execution):
        self.calls.append(execution)
        return BackendResult(
            work_fingerprint=execution.promotion.work_fingerprint,
            succeeded=True,
            outputs=("promoted-result",),
            evidence=("promotion-test",),
            classification="SUCCEEDED",
        )


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
    assert any(item["subject_id"] == "project-runner" for item in payload["selected"])
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _claim(tmp_path, *, head="a" * 40, ttl=60.0, now=1000.0):
    plan_path = _write_plan(tmp_path)
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    record = next(item for item in corpus.records if item.id == "project-runner")
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): head}
    )
    claim = claim_bound_plan_subject(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id="project-runner",
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-a",
        lease_ttl=ttl,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: now,
    )
    return claim, transport


def _evidence(
    claim,
    *,
    now=1000.0,
    review_valid_until=1100.0,
    execution_valid_until=1100.0,
    effect_class=NO_PROTECTED_EFFECT,
    include_effect=False,
    effect_valid_until=1100.0,
    reviewer="REZON",
):
    execution_request = None
    request_sha256 = None
    if effect_class == "SOURCE_WRITE":
        execution_request = {
            "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
            "operation": "PUT_FILE",
            "repository": claim.repository,
            "ref": claim.ref,
            "expected_head": claim.exact_head,
            "path": "docs/test-promotion.txt",
            "content": "test\n",
            "message": "Test promoted source write",
            "expected_blob_sha": None,
        }
        request_sha256 = source_write_request_sha256(execution_request)

    review = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_REVIEW_V1",
            "subject_id": claim.subject_id,
            "repository": claim.repository,
            "ref": claim.ref,
            "exact_head": claim.exact_head,
            "plan_sha256": claim.plan_sha256,
            "work_fingerprint": claim.work_fingerprint,
            "reviewer_identity": reviewer,
            "review_gate": "EXACT_HEAD_REVIEW",
            "review_state": "EXECUTION_PROMOTION_REVIEWED",
            "reviewed_at": now - 10.0,
            "valid_until": review_valid_until,
            "execution_request_sha256": request_sha256,
        },
        REVIEW_KEY,
    )
    execution = sign_evidence(
        {
            "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
            "grant_id": "exec-grant-1",
            "issuer": "test-execution-authority",
            "subject_id": claim.subject_id,
            "repository": claim.repository,
            "ref": claim.ref,
            "exact_head": claim.exact_head,
            "lineage_id": claim.lineage_id,
            "work_fingerprint": claim.work_fingerprint,
            "fencing_token": claim.fencing_token,
            "operation": "EXECUTE_FRONTIER",
            "effect_class": effect_class,
            "execution_request": execution_request,
            "execution_authorized": True,
            "issued_at": now - 5.0,
            "valid_until": execution_valid_until,
        },
        EXECUTION_KEY,
    )
    effect = None
    if include_effect:
        effect = sign_evidence(
            {
                "schema": "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_V1",
                "grant_id": "effect-grant-1",
                "issuer": "test-effect-authority",
                "subject_id": claim.subject_id,
                "repository": claim.repository,
                "ref": claim.ref,
                "exact_head": claim.exact_head,
                "lineage_id": claim.lineage_id,
                "work_fingerprint": claim.work_fingerprint,
                "fencing_token": claim.fencing_token,
                "effect_class": effect_class,
                "execution_request_sha256": request_sha256,
                "protected_effects_authorized": True,
                "issued_at": now - 4.0,
                "valid_until": effect_valid_until,
            },
            EFFECT_KEY,
        )
    return review, execution, effect


def _promote(
    tmp_path,
    claim,
    transport,
    review,
    execution,
    effect=None,
    *,
    clock=lambda: 1000.0,
):
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
        effect_authority_key=EFFECT_KEY if effect is not None else None,
        transport=transport,
        clock=clock,
    )


def _status(db_path, claim):
    with sqlite3.connect(db_path) as db:
        return db.execute(
            """
            SELECT status, generation
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (claim.lineage_id, claim.work_fingerprint),
        ).fetchone()


def test_fresh_exact_claim_promotes_to_running(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, effect = _evidence(claim)

    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )

    assert receipt.fencing_token == claim.fencing_token == 1
    assert receipt.exact_head == claim.exact_head
    assert receipt.effect_class == NO_PROTECTED_EFFECT
    assert receipt.effect_grant_sha256 is None
    assert receipt.promoted_work_generation == claim.work_generation + 1
    assert _status(tmp_path / "operator.sqlite3", claim) == ("RUNNING", 3)
    assert transport.mutations == []


def test_stale_source_refuses_promotion_and_leaves_claimed(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    transport.heads[(claim.repository, claim.ref)] = "b" * 40

    with pytest.raises(ValueError, match="source head is stale"):
        _promote(tmp_path, claim, transport, review, execution)

    assert _status(tmp_path / "operator.sqlite3", claim) == ("CLAIMED", 2)


def test_expired_fence_refuses_promotion(tmp_path):
    claim, transport = _claim(tmp_path, ttl=10.0)
    review, execution, _ = _evidence(claim, now=1011.0, review_valid_until=1100.0)

    with pytest.raises(ValueError, match="lease is expired"):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            clock=lambda: 1011.0,
        )
    assert _status(tmp_path / "operator.sqlite3", claim) == ("CLAIMED", 2)


def test_stale_review_refuses_promotion(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(
        claim,
        review_valid_until=1000.0,
    )

    with pytest.raises(ValueError, match="review evidence is stale"):
        _promote(tmp_path, claim, transport, review, execution)
    assert _status(tmp_path / "operator.sqlite3", claim) == ("CLAIMED", 2)


def test_unrequired_reviewer_refuses_promotion(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim, reviewer="NOT-A-REVIEWER")

    with pytest.raises(ValueError, match="reviewer identity is not required"):
        _promote(tmp_path, claim, transport, review, execution)


def test_bad_review_signature_refuses_promotion(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    review["valid_until"] = 1200.0

    with pytest.raises(ValueError, match="review evidence signature mismatch"):
        _promote(tmp_path, claim, transport, review, execution)


def test_bad_execution_authority_signature_refuses_promotion(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    execution["operation"] = "SOMETHING_ELSE"

    with pytest.raises(ValueError, match="execution authority grant signature mismatch"):
        _promote(tmp_path, claim, transport, review, execution)



def test_execution_operation_must_match_claimed_wave_action(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    execution["operation"] = "DIFFERENT_ACTION"
    execution = sign_evidence(execution, EXECUTION_KEY)

    with pytest.raises(ValueError, match="execution operation does not match claim action"):
        _promote(tmp_path, claim, transport, review, execution)


def test_protected_effect_requires_separate_authority(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(
        claim,
        effect_class="SOURCE_WRITE",
        include_effect=False,
    )

    with pytest.raises(
        ValueError,
        match="protected-effect authority is required separately",
    ):
        _promote(tmp_path, claim, transport, review, execution)


def test_separate_protected_effect_authority_can_promote(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, effect = _evidence(
        claim,
        effect_class="SOURCE_WRITE",
        include_effect=True,
    )

    receipt = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        effect,
    )
    assert receipt.effect_class == "SOURCE_WRITE"
    assert receipt.effect_grant_sha256 is not None


def test_protected_effect_authority_does_not_substitute_for_execution_authority(
    tmp_path,
):
    claim, transport = _claim(tmp_path)
    review, execution, effect = _evidence(
        claim,
        effect_class="SOURCE_WRITE",
        include_effect=True,
    )
    execution["execution_authorized"] = False
    execution = sign_evidence(execution, EXECUTION_KEY)

    with pytest.raises(ValueError, match="does not authorize execution"):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            effect,
        )



def test_source_only_ceiling_rejects_deploy_even_with_effect_grant(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, effect = _evidence(
        claim,
        effect_class="DEPLOY",
        include_effect=True,
    )

    with pytest.raises(ValueError, match="exceeds claim effect ceiling"):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            effect,
        )


def test_execution_authority_key_cannot_verify_effect_grant(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, effect = _evidence(
        claim,
        effect_class="SOURCE_WRITE",
        include_effect=True,
    )

    with pytest.raises(ValueError, match="protected-effect authority grant signature mismatch"):
        promote_claimed_to_running(
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
            effect_authority_key=EXECUTION_KEY,
            transport=transport,
            clock=lambda: 1000.0,
        )




def test_legacy_no_request_promotion_digest_remains_replayable(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    receipt = _promote(tmp_path, claim, transport, review, execution)
    assert receipt.execution_request_sha256 is None

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        row = db.execute(
            """
            SELECT holder, repository, ref, exact_head, operation, effect_class,
                   review_sha256, review_valid_until,
                   execution_grant_sha256, execution_valid_until,
                   effect_grant_sha256, effect_valid_until,
                   promoted_at, attempt_work_generation,
                   promoted_work_generation
            FROM execution_promotions
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        ).fetchone()
        legacy_payload = {
            "schema": "PROJECT_RUNNER_EXECUTION_PROMOTION_V1",
            "lineage_id": claim.lineage_id,
            "work_fingerprint": claim.work_fingerprint,
            "fencing_token": claim.fencing_token,
            "holder": row[0],
            "repository": row[1],
            "ref": row[2],
            "exact_head": row[3],
            "operation": row[4],
            "effect_class": row[5],
            "review_sha256": row[6],
            "review_valid_until": row[7],
            "execution_grant_sha256": row[8],
            "execution_valid_until": row[9],
            "effect_grant_sha256": row[10],
            "effect_valid_until": row[11],
            "promoted_at": row[12],
            "attempt_work_generation": row[13],
            "promoted_work_generation": row[14],
        }
        canonical = json.dumps(
            legacy_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        legacy_digest = hashlib.sha256(canonical).hexdigest()
        db.execute(
            """
            UPDATE execution_promotions
            SET promotion_sha256 = ?
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                legacy_digest,
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        )

    replayed = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        clock=lambda: 1001.0,
    )
    assert replayed.promotion_sha256 == legacy_digest
    assert replayed.execution_request_sha256 is None


def test_committed_promotion_replay_returns_same_receipt(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    first = _promote(tmp_path, claim, transport, review, execution)
    second = _promote(
        tmp_path,
        claim,
        transport,
        review,
        execution,
        clock=lambda: 1001.0,
    )
    assert second == first
    assert _status(tmp_path / "operator.sqlite3", claim) == ("RUNNING", 3)


def test_committed_promotion_replay_rejects_changed_authority(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    _promote(tmp_path, claim, transport, review, execution)

    changed = dict(execution)
    changed["grant_id"] = "exec-grant-2"
    changed = sign_evidence(changed, EXECUTION_KEY)

    with pytest.raises(ValueError, match="replay authority mismatch"):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            changed,
            clock=lambda: 1001.0,
        )


def test_committed_promotion_replay_rechecks_live_head(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    _promote(tmp_path, claim, transport, review, execution)
    transport.heads[(claim.repository, claim.ref)] = "9" * 40

    with pytest.raises(ValueError, match="replay source head is stale"):
        _promote(
            tmp_path,
            claim,
            transport,
            review,
            execution,
            clock=lambda: 1001.0,
        )


def test_execute_promoted_rechecks_source_and_does_not_call_backend_when_stale(
    tmp_path,
):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    receipt = _promote(tmp_path, claim, transport, review, execution)
    transport.heads[(claim.repository, claim.ref)] = "c" * 40
    backend = SpyPromotedBackend()

    with pytest.raises(ValueError, match="source head is stale"):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=backend,
            transport=transport,
            clock=lambda: 1001.0,
        )

    assert backend.calls == []


def test_execute_promoted_refuses_expired_fence_before_backend(tmp_path):
    claim, transport = _claim(tmp_path, ttl=10.0)
    review, execution, _ = _evidence(
        claim,
        review_valid_until=1200.0,
        execution_valid_until=1200.0,
    )
    receipt = _promote(tmp_path, claim, transport, review, execution)
    backend = SpyPromotedBackend()

    with pytest.raises(ValueError, match="lease is expired"):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=backend,
            transport=transport,
            clock=lambda: 1011.0,
        )
    assert backend.calls == []


def test_execute_promoted_refuses_review_that_became_stale(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(
        claim,
        review_valid_until=1005.0,
        execution_valid_until=1200.0,
    )
    receipt = _promote(tmp_path, claim, transport, review, execution)
    backend = SpyPromotedBackend()

    with pytest.raises(ValueError, match="review is stale"):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=backend,
            transport=transport,
            clock=lambda: 1006.0,
        )
    assert backend.calls == []


def test_execute_promoted_calls_backend_only_after_all_rechecks(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(
        claim,
        review_valid_until=1200.0,
        execution_valid_until=1200.0,
    )
    receipt = _promote(tmp_path, claim, transport, review, execution)
    backend = SpyPromotedBackend()
    result = execute_promoted(
        state_db=tmp_path / "operator.sqlite3",
        receipt=receipt,
        backend=backend,
        transport=transport,
        clock=lambda: 1001.0,
    )

    assert result.succeeded is True
    assert len(backend.calls) == 1
    promoted = backend.calls[0]
    assert promoted.operation == "EXECUTE_FRONTIER"
    assert promoted.effect_class == NO_PROTECTED_EFFECT
    assert promoted.work.status.value == "RUNNING"

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        result_row = db.execute(
            """
            SELECT result_sha256
            FROM execution_attempts
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        ).fetchone()
    assert result_row is not None and result_row[0]



def test_supplied_promotion_receipt_fields_cannot_diverge_from_durable_row(tmp_path):
    from dataclasses import replace

    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    receipt = _promote(tmp_path, claim, transport, review, execution)
    forged = replace(receipt, review_valid_until=receipt.review_valid_until + 1000.0)
    backend = SpyPromotedBackend()

    with pytest.raises(ValueError, match="receipt does not match durable state"):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=forged,
            backend=backend,
            transport=transport,
            clock=lambda: 1001.0,
        )
    assert backend.calls == []


def test_tampered_durable_promotion_refuses_backend(tmp_path):
    claim, transport = _claim(tmp_path)
    review, execution, _ = _evidence(claim)
    receipt = _promote(tmp_path, claim, transport, review, execution)
    backend = SpyPromotedBackend()

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        db.execute(
            """
            UPDATE execution_promotions
            SET exact_head = ?
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (
                "d" * 40,
                claim.lineage_id,
                claim.work_fingerprint,
                claim.fencing_token,
            ),
        )

    with pytest.raises(ValueError, match="promotion digest mismatch"):
        execute_promoted(
            state_db=tmp_path / "operator.sqlite3",
            receipt=receipt,
            backend=backend,
            transport=transport,
            clock=lambda: 1001.0,
        )
    assert backend.calls == []
