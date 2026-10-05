from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .backends import BackendResult
from .dispatch import DispatchAttempt
from .leases import Lease, LeaseStore
from .models import ExactSubject
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


@dataclass(frozen=True)
class VerificationOutcome:
    work: WorkUnit
    status: WorkUnitStatus
    reason: str


SubjectReader = Callable[[ExactSubject], ExactSubject | None]
EvidenceVerifier = Callable[[WorkUnit, BackendResult], bool]


def verify_observed_result(
    *,
    work: WorkUnit,
    lease: Lease,
    result: BackendResult,
    lease_store: LeaseStore,
    now: float,
    current_subject_reader: SubjectReader,
    evidence_verifier: EvidenceVerifier,
    manage_lease: bool = True,
) -> VerificationOutcome:
    """Verify an already-observed backend result.

    Recovery callers can set manage_lease=False to re-check currentness and
    completion evidence without mutating lease state or re-executing a backend.
    """
    if result.work_fingerprint != __import__(
        "runner.work_units", fromlist=["work_unit_fingerprint"]
    ).work_unit_fingerprint(work):
        raise ValueError("backend result fingerprint does not match work")

    if not result.succeeded:
        if manage_lease:
            lease_store.release(lease, now=now)
        if result.classification == "PRECONDITION_FAILED":
            return VerificationOutcome(
                work=work,
                status=WorkUnitStatus.SUPERSEDED,
                reason="backend precondition no longer matches exact subject",
            )
        return VerificationOutcome(
            work=work,
            status=WorkUnitStatus.FAILED_DETERMINISTIC,
            reason="backend reported failure",
        )

    for original in work.inputs:
        current = current_subject_reader(original)
        if current is None:
            return VerificationOutcome(
                work=work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="exact current subject could not be established",
            )
        if current.identity() != original.identity():
            if manage_lease:
                lease_store.release(lease, now=now)
            return VerificationOutcome(
                work=work,
                status=WorkUnitStatus.SUPERSEDED,
                reason="exact subject moved after execution",
            )

    if not evidence_verifier(work, result):
        return VerificationOutcome(
            work=work,
            status=WorkUnitStatus.VERIFYING,
            reason="worker/backend claim requires independent completion evidence",
        )

    if manage_lease and not lease_store.complete(lease, now=now):
        return VerificationOutcome(
            work=work,
            status=WorkUnitStatus.OUTCOME_UNKNOWN,
            reason="lease/fence no longer authorizes completion",
        )

    return VerificationOutcome(
        work=work,
        status=WorkUnitStatus.COMPLETE,
        reason="exact subject current and completion evidence verified",
    )


def verify_attempt(
    attempt: DispatchAttempt,
    *,
    lease_store: LeaseStore,
    now: float,
    current_subject_reader: SubjectReader,
    evidence_verifier: EvidenceVerifier,
    manage_lease: bool = True,
) -> VerificationOutcome:
    return verify_observed_result(
        work=attempt.work,
        lease=attempt.lease,
        result=attempt.result,
        lease_store=lease_store,
        now=now,
        current_subject_reader=current_subject_reader,
        evidence_verifier=evidence_verifier,
        manage_lease=manage_lease,
    )
