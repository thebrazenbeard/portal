from pathlib import Path

import pytest

from runner.backends import BackendResult
from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.models import ExactSubject
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore
from runner.recovery import (
    RecoveryAction,
    load_recovery_snapshots,
    reverify_recovery_snapshot,
)
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _seed_attempt(db: Path):
    work = WorkUnit(
        id="recovery-root",
        root_frontier_id="frontier-recovery",
        parent_work_id=None,
        inputs=(
            ExactSubject(
                repository="thebrazenbeard/project-runner",
                ref="work/public-safe-portfolio-registry-v2",
                commit="a" * 40,
                path="runner/recovery.py",
            ),
        ),
        operation="INSPECT",
        required_capabilities=("read", "analyze"),
        collision_keys=("project:project-runner",),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 1,
            "backend_jobs": 1,
        },
        expected_outputs=("backend-result",),
        completion_criteria=("verified",),
        status=WorkUnitStatus.PENDING,
        payload={"mode": "restart-recovery-test"},
    )
    fingerprint = work_unit_fingerprint(work)
    budget = BudgetEnvelope(
        lineage_id="recovery-lineage",
        max_depth=2,
        depth=0,
        remaining_children=0,
        remaining_active=2,
        remaining_retries=1,
        remaining_backend_jobs=2,
    )

    budgets = SqliteBudgetStore(db)
    assert budgets.put_initial(budget) == 1
    budgets.close()

    works = SqliteRecursiveWorkStore(db)
    works.put_initial(
        work=work,
        lineage_id=budget.lineage_id,
        budget_scope_id=budget.scope_id,
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    works.close()

    journal = SqliteDispatchAdmissionStore(db)
    admitted = journal.admit(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        budget_scope_id=budget.scope_id,
        expected_budget_generation=1,
        expected_work_generation=1,
        holder="worker-a",
        now=10.0,
        ttl=30.0,
    )
    journal.close()
    return work, fingerprint, budget, admitted


def _result(fingerprint: str) -> BackendResult:
    return BackendResult(
        work_fingerprint=fingerprint,
        succeeded=True,
        outputs=("backend-result",),
        evidence=("durable-observation",),
        classification="SUCCEEDED",
    )


def test_admitted_restart_requires_effect_reconciliation(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, _, _, _ = _seed_attempt(db)

    snapshots = load_recovery_snapshots(db, now=11.0)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.action is RecoveryAction.RECONCILE_EFFECT
    assert snapshot.result is None
    assert snapshot.backend_reexecution_allowed is False
    assert snapshot.fence_current is True
    assert snapshot.fence_live is True

    leases = SqliteLeaseStore(db)
    with pytest.raises(ValueError, match="effect reconciliation"):
        reverify_recovery_snapshot(
            snapshot,
            lease_store=leases,
            now=11.0,
            current_subject_reader=lambda subject: subject,
            evidence_verifier=lambda work, result: True,
        )
    leases.close()


def test_recorded_result_reverifies_without_backend_execution(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    journal.record_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        result=_result(fingerprint),
        recorded_at=11.0,
    )
    journal.close()

    snapshot = load_recovery_snapshots(db, now=12.0)[0]
    assert snapshot.action is RecoveryAction.REVERIFY_RECORDED_RESULT
    assert snapshot.result == _result(fingerprint)
    assert snapshot.backend_reexecution_allowed is False

    leases = SqliteLeaseStore(db)
    verification = reverify_recovery_snapshot(
        snapshot,
        lease_store=leases,
        now=12.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: True,
    )
    leases.close()

    assert verification.outcome.status is WorkUnitStatus.COMPLETE
    assert verification.terminalization_authorized is True


def test_recorded_result_with_expired_fence_cannot_terminalize(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    journal.record_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        result=_result(fingerprint),
        recorded_at=11.0,
    )
    journal.close()

    snapshot = load_recovery_snapshots(db, now=50.0)[0]
    assert snapshot.fence_current is True
    assert snapshot.fence_live is False

    leases = SqliteLeaseStore(db)
    verification = reverify_recovery_snapshot(
        snapshot,
        lease_store=leases,
        now=50.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: True,
    )
    leases.close()

    assert verification.outcome.status is WorkUnitStatus.COMPLETE
    assert verification.terminalization_authorized is False
    assert "separate reconciliation" in verification.reason


def test_outcome_unknown_is_restart_visible_and_reverifiable(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    journal.record_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        result=_result(fingerprint),
        recorded_at=11.0,
    )
    assert journal.record_verification(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        status=WorkUnitStatus.OUTCOME_UNKNOWN,
        reason="current subject temporarily unavailable",
        verified_at=12.0,
    ) == 1
    journal.close()

    snapshot = load_recovery_snapshots(db, now=13.0)[0]
    assert snapshot.action is RecoveryAction.REVERIFY_OUTCOME_UNKNOWN

    leases = SqliteLeaseStore(db)
    verification = reverify_recovery_snapshot(
        snapshot,
        lease_store=leases,
        now=13.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: True,
    )
    leases.close()

    assert verification.outcome.status is WorkUnitStatus.COMPLETE
    assert verification.terminalization_authorized is True


def test_no_effect_reconciliation_atomically_enables_bounded_retry(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    next_generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="NO_EFFECT_CONFIRMED",
        reason="provider idempotency ledger confirms no operation was applied",
        evidence=("provider-ledger:op-123=ABSENT",),
        reconciler="provider-reconciler",
        observed_at=50.0,
    )
    assert next_generation == admitted.work_generation + 1
    assert journal.unresolved_attempts() == ()

    stored = journal.connection.execute(
        """
        SELECT status, generation
        FROM recursive_work_state
        WHERE lineage_id = ? AND work_fingerprint = ?
        """,
        (budget.lineage_id, fingerprint),
    ).fetchone()
    assert stored == (WorkUnitStatus.FAILED_RETRYABLE.value, next_generation)

    lease_row = journal.connection.execute(
        """
        SELECT holder, fencing_token, expires_at, completed
        FROM leases WHERE work_fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    assert lease_row == (None, admitted.lease.fencing_token, 0.0, 0)

    retry = journal.admit(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        budget_scope_id=budget.scope_id,
        expected_budget_generation=admitted.budget_generation,
        expected_work_generation=next_generation,
        holder="worker-b",
        now=51.0,
        ttl=30.0,
    )
    journal.close()

    assert retry.lease.fencing_token == admitted.lease.fencing_token + 1
    assert retry.retry_consumed == 1
    assert retry.budget_after.remaining_retries == 0
    assert retry.budget_after.remaining_backend_jobs == 0


def test_effect_confirmed_reconciliation_blocks_backend_retry(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    unchanged_generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="EFFECT_CONFIRMED",
        reason="provider audit log proves the operation was applied",
        evidence=("provider-audit:op-123=APPLIED",),
        reconciler="provider-reconciler",
        observed_at=50.0,
    )
    journal.close()

    assert unchanged_generation == admitted.work_generation
    snapshot = load_recovery_snapshots(db, now=51.0)[0]
    assert snapshot.action is RecoveryAction.RECONSTRUCT_CONFIRMED_EFFECT
    assert snapshot.entry.last_reconciliation_outcome == "EFFECT_CONFIRMED"
    assert snapshot.result is None
    assert snapshot.backend_reexecution_allowed is False

    leases = SqliteLeaseStore(db)
    with pytest.raises(ValueError, match="durable result reconstruction"):
        reverify_recovery_snapshot(
            snapshot,
            lease_store=leases,
            now=51.0,
            current_subject_reader=lambda subject: subject,
            evidence_verifier=lambda work, result: True,
        )
    leases.close()

    journal = SqliteDispatchAdmissionStore(db)
    with pytest.raises(ValueError, match="not dispatch-admissible"):
        journal.admit(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=budget.scope_id,
            expected_budget_generation=admitted.budget_generation,
            expected_work_generation=admitted.work_generation,
            holder="worker-b",
            now=52.0,
            ttl=30.0,
        )
    journal.close()


def test_indeterminate_reconciliation_can_be_refined_but_conclusion_is_immutable(
    tmp_path: Path,
):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="INDETERMINATE",
        reason="first provider read timed out",
        evidence=("provider-read:timeout",),
        reconciler="provider-reconciler",
        observed_at=50.0,
    )
    assert generation == admitted.work_generation
    unresolved = journal.unresolved_attempts()
    assert len(unresolved) == 1
    assert unresolved[0].phase == "ADMITTED"
    assert unresolved[0].last_reconciliation_outcome == "INDETERMINATE"

    next_generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="NO_EFFECT_CONFIRMED",
        reason="second provider read confirms no operation",
        evidence=("provider-ledger:op-123=ABSENT",),
        reconciler="provider-reconciler",
        observed_at=51.0,
    )
    assert next_generation == admitted.work_generation + 1

    with pytest.raises(ValueError, match="conclusive execution reconciliation"):
        journal.reconcile_admitted(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=admitted.lease.fencing_token,
            expected_work_generation=next_generation,
            lease=admitted.lease,
            outcome="EFFECT_CONFIRMED",
            reason="contradictory late assertion",
            evidence=("late-assertion:APPLIED",),
            reconciler="other-reconciler",
            observed_at=52.0,
        )
    journal.close()


def test_reconciliation_after_result_recording_is_rejected(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    journal.record_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        result=_result(fingerprint),
        recorded_at=11.0,
    )
    with pytest.raises(ValueError, match="invalid after result recording"):
        journal.reconcile_admitted(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=admitted.lease.fencing_token,
            expected_work_generation=admitted.work_generation,
            lease=admitted.lease,
            outcome="NO_EFFECT_CONFIRMED",
            reason="too late",
            evidence=("provider-ledger:ABSENT",),
            reconciler="provider-reconciler",
            observed_at=12.0,
        )
    journal.close()


def test_late_result_cannot_reopen_no_effect_reconciled_attempt(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    next_generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="NO_EFFECT_CONFIRMED",
        reason="provider confirms no effect",
        evidence=("provider-ledger:ABSENT",),
        reconciler="provider-reconciler",
        observed_at=50.0,
    )
    assert next_generation == admitted.work_generation + 1

    with pytest.raises(ValueError, match="no-effect reconciliation"):
        journal.record_result(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=admitted.lease.fencing_token,
            result=_result(fingerprint),
            recorded_at=51.0,
        )
    journal.close()


def test_late_result_may_resolve_indeterminate_reconciliation(tmp_path: Path):
    db = tmp_path / "recovery.db"
    _, fingerprint, budget, admitted = _seed_attempt(db)

    journal = SqliteDispatchAdmissionStore(db)
    generation = journal.reconcile_admitted(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        expected_work_generation=admitted.work_generation,
        lease=admitted.lease,
        outcome="INDETERMINATE",
        reason="provider read timed out",
        evidence=("provider-read:timeout",),
        reconciler="provider-reconciler",
        observed_at=50.0,
    )
    assert generation == admitted.work_generation

    result = _result(fingerprint)
    journal.record_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
        result=result,
        recorded_at=51.0,
    )
    assert journal.load_result(
        lineage_id=budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=admitted.lease.fencing_token,
    ) == result
    assert journal.unresolved_attempts()[0].phase == "RESULT_RECORDED"
    journal.close()
