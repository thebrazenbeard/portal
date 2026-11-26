from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .backends import BackendResult
from .durable_dispatch import (
    DurableExecutionJournalEntry,
    SqliteDispatchAdmissionStore,
)
from .leases import Lease, LeaseStore
from .recursive_state import SqliteRecursiveWorkStore
from .verify import (
    EvidenceVerifier,
    SubjectReader,
    VerificationOutcome,
    verify_observed_result,
)
from .work_units import WorkUnit, WorkUnitStatus


_TERMINAL_RECOVERY_STATUSES = frozenset(
    {
        WorkUnitStatus.COMPLETE,
        WorkUnitStatus.FAILED_DETERMINISTIC,
        WorkUnitStatus.SUPERSEDED,
    }
)


class RecoveryAction(str, Enum):
    RECONCILE_EFFECT = "RECONCILE_EFFECT"
    RECONSTRUCT_CONFIRMED_EFFECT = "RECONSTRUCT_CONFIRMED_EFFECT"
    REVERIFY_RECORDED_RESULT = "REVERIFY_RECORDED_RESULT"
    REVERIFY_OUTCOME_UNKNOWN = "REVERIFY_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class RecoverySnapshot:
    entry: DurableExecutionJournalEntry
    work: WorkUnit
    result: BackendResult | None
    lease: Lease
    action: RecoveryAction
    fence_current: bool
    fence_live: bool
    backend_reexecution_allowed: bool = False


@dataclass(frozen=True)
class RecoveryVerification:
    outcome: VerificationOutcome
    terminalization_authorized: bool
    reason: str


def load_recovery_snapshots(
    path: str | Path,
    *,
    now: float,
) -> tuple[RecoverySnapshot, ...]:
    """Load unresolved execution attempts without executing any backend work.

    ADMITTED is deliberately treated as an ambiguous-effect state. Recovery
    never assumes that a missing result means the backend did nothing.
    RESULT_RECORDED and OUTCOME_UNKNOWN preserve the exact durable result and
    can be re-verified without backend execution.
    """
    journal = SqliteDispatchAdmissionStore(path)
    works = SqliteRecursiveWorkStore(path)
    snapshots: list[RecoverySnapshot] = []
    try:
        for entry in journal.unresolved_attempts():
            stored = works.get(entry.lineage_id, entry.work_fingerprint)
            if stored is None:
                raise ValueError("recovery work state not found")

            lease_row = journal.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases
                WHERE work_fingerprint = ?
                """,
                (entry.work_fingerprint,),
            ).fetchone()
            if lease_row is None:
                raise ValueError("recovery lease state not found")

            current_holder, current_token, current_expiry, completed = lease_row
            fence_current = (
                current_holder == entry.holder
                and int(current_token) == entry.fencing_token
                and not bool(completed)
            )
            fence_live = fence_current and now < float(current_expiry)
            lease = Lease(
                work_fingerprint=entry.work_fingerprint,
                holder=entry.holder,
                fencing_token=entry.fencing_token,
                expires_at=float(current_expiry) if fence_current else 0.0,
            )

            if entry.phase == "ADMITTED":
                result = None
                action = RecoveryAction.RECONCILE_EFFECT
            elif entry.phase == "EFFECT_CONFIRMED":
                result = None
                action = RecoveryAction.RECONSTRUCT_CONFIRMED_EFFECT
            elif entry.phase == "RESULT_RECORDED":
                result = journal.load_result(
                    lineage_id=entry.lineage_id,
                    work_fingerprint_value=entry.work_fingerprint,
                    fencing_token=entry.fencing_token,
                )
                if result is None:
                    raise ValueError("recorded-result recovery is missing its result")
                action = RecoveryAction.REVERIFY_RECORDED_RESULT
            elif entry.phase == "OUTCOME_UNKNOWN":
                result = journal.load_result(
                    lineage_id=entry.lineage_id,
                    work_fingerprint_value=entry.work_fingerprint,
                    fencing_token=entry.fencing_token,
                )
                if result is None:
                    raise ValueError("unknown-outcome recovery is missing its result")
                action = RecoveryAction.REVERIFY_OUTCOME_UNKNOWN
            else:
                raise ValueError(f"unsupported recovery phase: {entry.phase}")

            snapshots.append(
                RecoverySnapshot(
                    entry=entry,
                    work=stored.work,
                    result=result,
                    lease=lease,
                    action=action,
                    fence_current=fence_current,
                    fence_live=fence_live,
                )
            )
    finally:
        works.close()
        journal.close()

    return tuple(snapshots)


def reverify_recovery_snapshot(
    snapshot: RecoverySnapshot,
    *,
    lease_store: LeaseStore,
    now: float,
    current_subject_reader: SubjectReader,
    evidence_verifier: EvidenceVerifier,
) -> RecoveryVerification:
    """Re-verify durable evidence without executing a backend or mutating lease state."""
    if snapshot.result is None:
        if snapshot.action is RecoveryAction.RECONSTRUCT_CONFIRMED_EFFECT:
            raise ValueError(
                "confirmed-effect recovery requires durable result reconstruction; "
                "backend re-execution is forbidden"
            )
        raise ValueError(
            "ADMITTED recovery requires effect reconciliation before retry; "
            "backend re-execution is forbidden"
        )

    outcome = verify_observed_result(
        work=snapshot.work,
        lease=snapshot.lease,
        result=snapshot.result,
        lease_store=lease_store,
        now=now,
        current_subject_reader=current_subject_reader,
        evidence_verifier=evidence_verifier,
        manage_lease=False,
    )

    terminalization_authorized = (
        snapshot.fence_live and outcome.status in _TERMINAL_RECOVERY_STATUSES
    )
    if terminalization_authorized:
        reason = "exact recorded result reverified under the current live fence"
    elif outcome.status in _TERMINAL_RECOVERY_STATUSES:
        reason = (
            "recorded result reverified, but the original fence is not live/current; "
            "terminalization requires separate reconciliation"
        )
    else:
        reason = "recorded result reverified without terminal authority"

    return RecoveryVerification(
        outcome=outcome,
        terminalization_authorized=terminalization_authorized,
        reason=reason,
    )
