from __future__ import annotations

from dataclasses import replace
import hashlib

from .backends import BackendResult
from .dispatch import DispatchAttempt
from .github_backend import GitHubBackend, GitHubOperation, GitHubRequest
from .leases import LeaseStore
from .models import ExactSubject, Frontier
from .verify import VerificationOutcome
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _require_ref_subject(subject: ExactSubject) -> None:
    if not subject.repository or not subject.ref or not subject.commit:
        raise ValueError(
            "M6 GitHub inspection requires repository, ref, and exact commit"
        )


def frontier_to_github_inspection_work(
    frontier: Frontier,
    target_subject: ExactSubject,
    depth: int = 0,
    *,
    registry_digest: str | None = None,
) -> WorkUnit:
    if frontier.work_type != "INSPECT":
        raise ValueError("M6 GitHub inspection factory only accepts INSPECT frontiers")
    _require_ref_subject(frontier.subject)
    _require_ref_subject(target_subject)

    inputs = (frontier.subject,)
    if target_subject.identity() != frontier.subject.identity():
        inputs = inputs + (target_subject,)

    m6_payload: dict[str, str] = {"kind": "github-read-inspection"}
    if registry_digest is not None:
        if (
            len(registry_digest) != 64
            or any(character not in "0123456789abcdef" for character in registry_digest)
        ):
            raise ValueError("project registry digest must be lowercase SHA-256")
        m6_payload["project_registry_sha256"] = registry_digest

    return WorkUnit(
        id=f"m6-read-{frontier.id}",
        root_frontier_id=frontier.id,
        parent_work_id=None,
        inputs=inputs,
        operation="INSPECT",
        required_capabilities=frontier.required_capabilities,
        collision_keys=frontier.collision_keys,
        recursion_depth=depth,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        expected_outputs=("read-ref-ok",),
        completion_criteria=(
            "exact-subject-current",
            "completion-evidence-verified",
        ),
        status=WorkUnitStatus.PENDING,
        payload={"m6": m6_payload},
    )


def _github_read_work(subject: ExactSubject, *, expected: bool) -> WorkUnit:
    _require_ref_subject(subject)
    request: dict[str, str] = {
        "operation": "READ_REF",
        "repository": subject.repository,
        "ref": subject.ref,
    }
    if expected:
        request["expected_head"] = subject.commit

    return WorkUnit(
        id="m6-internal-github-read",
        root_frontier_id="m6-internal-github-read",
        parent_work_id=None,
        inputs=(subject,),
        operation="GITHUB",
        required_capabilities=("github.read_ref",),
        collision_keys=(),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 0,
            "retries": 0,
            "backend_jobs": 0,
        },
        expected_outputs=("github-ref-head",),
        completion_criteria=("github-readback-verified",),
        status=WorkUnitStatus.RUNNING,
        payload={"github": request},
    )


def _redacted_failure(work: WorkUnit, classification: str) -> BackendResult:
    return BackendResult(
        work_fingerprint=work_unit_fingerprint(work),
        succeeded=False,
        outputs=(),
        evidence=(
            "m6:github-read-inspection",
            f"github:{classification.lower()}",
        ),
        classification=classification,
    )


class GitHubReadInspectionBackend:
    """Read-only M6 adapter with explicit route + target-authority enforcement.

    The wrapped GitHub backend performs the actual authority checks and ref reads.
    This adapter intentionally does not propagate repository, ref, commit, or
    backend failure-detail strings into its result evidence.
    """

    def __init__(self, backend: GitHubBackend) -> None:
        self.backend = backend

    def execute(self, work: WorkUnit) -> BackendResult:
        if work.operation != "INSPECT":
            return _redacted_failure(work, "UNSUPPORTED_OPERATION")
        if not work.inputs:
            return _redacted_failure(work, "INVALID_REQUEST")

        for subject in work.inputs:
            try:
                inner = _github_read_work(subject, expected=True)
            except ValueError:
                return _redacted_failure(work, "INVALID_REQUEST")
            result = self.backend.execute(inner)
            if not result.succeeded:
                return _redacted_failure(work, result.classification)

        return BackendResult(
            work_fingerprint=work_unit_fingerprint(work),
            succeeded=True,
            outputs=tuple("READ_REF_OK" for _ in work.inputs),
            evidence=(
                "m6:github-read-inspection",
                "github:readback-verified",
                f"subjects={len(work.inputs)}",
            ),
            classification="SUCCEEDED",
        )


class GitHubCurrentSubjectReader:
    """Independent exact-head reader using the authority-gated GitHub backend."""

    def __init__(self, backend: GitHubBackend) -> None:
        self.backend = backend

    def read(self, subject: ExactSubject) -> ExactSubject | None:
        try:
            inner = _github_read_work(subject, expected=False)
        except ValueError:
            return None
        result = self.backend.execute(inner)
        if not result.succeeded or len(result.outputs) != 1:
            return None
        observed = result.outputs[0]
        if not observed:
            return None
        return replace(subject, commit=observed)


def _github_file_read_work(
    *,
    repository: str,
    ref: str,
    path: str,
    expected_blob_sha: str,
    expected_head: str | None = None,
) -> WorkUnit:
    subject = ExactSubject(repository=repository, ref=ref)
    return WorkUnit(
        id="m6-internal-github-file-read",
        root_frontier_id="m6-internal-github-file-read",
        parent_work_id=None,
        inputs=(subject,),
        operation="GITHUB",
        required_capabilities=("github.read_file",),
        collision_keys=(),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 0,
            "retries": 0,
            "backend_jobs": 0,
        },
        expected_outputs=("github-file-state",),
        completion_criteria=("github-readback-verified",),
        status=WorkUnitStatus.RUNNING,
        payload={
            "github": {
                "operation": "READ_FILE",
                "repository": repository,
                "ref": ref,
                "path": path,
                "expected_blob_sha": expected_blob_sha,
                **({"expected_head": expected_head} if expected_head is not None else {}),
            }
        },
    )


def _mutation_failure_status(classification: str) -> WorkUnitStatus:
    if classification == "PRECONDITION_FAILED":
        return WorkUnitStatus.SUPERSEDED
    if classification in {
        "AUTHORITY_DENIED",
        "INVALID_REQUEST",
        "NOT_FOUND",
        "ROUTE_UNAVAILABLE",
        "UNSUPPORTED_OPERATION",
    }:
        return WorkUnitStatus.FAILED_DETERMINISTIC
    return WorkUnitStatus.OUTCOME_UNKNOWN


def _matching_precondition_input(work: WorkUnit, request: GitHubRequest) -> bool:
    if request.expected_head is None:
        return False
    if request.operation is GitHubOperation.CREATE_BRANCH:
        if request.source_ref is None:
            return False
        return any(
            subject.repository == request.repository
            and subject.ref == request.source_ref
            and subject.commit == request.expected_head
            for subject in work.inputs
        )
    return any(
        subject.repository == request.repository
        and subject.ref == request.ref
        and subject.commit == request.expected_head
        for subject in work.inputs
    )


def verify_github_mutation_attempt(
    attempt: DispatchAttempt,
    *,
    lease_store: LeaseStore,
    now: float,
    verification_backend: GitHubBackend,
) -> VerificationOutcome:
    """Verify mutation postconditions without requiring the old target head to survive."""

    try:
        raw = attempt.work.payload.get("github")
        if not isinstance(raw, dict):
            raise ValueError("missing github request")
        request = GitHubRequest.from_mapping(raw)
    except (KeyError, TypeError, ValueError):
        lease_store.release(attempt.lease, now=now)
        return VerificationOutcome(
            work=attempt.work,
            status=WorkUnitStatus.FAILED_DETERMINISTIC,
            reason="mutation request is structurally invalid",
        )

    if request.operation not in {
        GitHubOperation.CREATE_BRANCH,
        GitHubOperation.PUT_FILE,
    }:
        lease_store.release(attempt.lease, now=now)
        return VerificationOutcome(
            work=attempt.work,
            status=WorkUnitStatus.FAILED_DETERMINISTIC,
            reason="mutation verifier received unsupported operation",
        )

    if not _matching_precondition_input(attempt.work, request):
        return VerificationOutcome(
            work=attempt.work,
            status=WorkUnitStatus.OUTCOME_UNKNOWN,
            reason="mutation precondition is not bound to an exact work input",
        )

    if not attempt.result.succeeded:
        status = _mutation_failure_status(attempt.result.classification)
        if status in {
            WorkUnitStatus.FAILED_DETERMINISTIC,
            WorkUnitStatus.SUPERSEDED,
        }:
            lease_store.release(attempt.lease, now=now)
        if status is WorkUnitStatus.SUPERSEDED:
            reason = "mutation precondition moved before execution"
        elif status is WorkUnitStatus.FAILED_DETERMINISTIC:
            reason = "mutation was rejected before a supported effect"
        else:
            reason = "mutation outcome requires reconciliation"
        return VerificationOutcome(
            work=attempt.work,
            status=status,
            reason=reason,
        )

    if request.operation is GitHubOperation.CREATE_BRANCH:
        if request.expected_head is None or len(attempt.result.outputs) != 1:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="branch creation result is incomplete",
            )
        target = ExactSubject(
            repository=request.repository,
            ref=request.ref,
            commit=request.expected_head,
        )
        readback = verification_backend.execute(
            _github_read_work(target, expected=False)
        )
        if not readback.succeeded or len(readback.outputs) != 1:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="independent branch readback is unavailable",
            )
        if readback.outputs[0] != attempt.result.outputs[0]:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.SUPERSEDED,
                reason="created branch moved after execution",
            )
    else:
        if (
            request.path is None
            or request.content is None
            or len(attempt.result.outputs) != 2
        ):
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="file mutation result is incomplete",
            )
        new_commit, new_blob = attempt.result.outputs
        target = ExactSubject(
            repository=request.repository,
            ref=request.ref,
            commit=new_commit,
        )
        ref_readback = verification_backend.execute(
            _github_read_work(target, expected=False)
        )
        if not ref_readback.succeeded or len(ref_readback.outputs) != 1:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="independent head readback is unavailable",
            )
        if ref_readback.outputs[0] != new_commit:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.SUPERSEDED,
                reason="mutated branch moved after execution",
            )

        file_readback = verification_backend.execute(
            _github_file_read_work(
                repository=request.repository,
                ref=request.ref,
                path=request.path,
                expected_blob_sha=new_blob,
                expected_head=new_commit,
            )
        )
        if not file_readback.succeeded or len(file_readback.outputs) != 2:
            if file_readback.classification == "PRECONDITION_FAILED":
                return VerificationOutcome(
                    work=attempt.work,
                    status=WorkUnitStatus.SUPERSEDED,
                    reason="mutated branch moved during independent verification",
                )
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="independent file readback is unavailable",
            )
        observed_blob, observed_content_digest = file_readback.outputs
        expected_content_digest = hashlib.sha256(
            request.content.encode("utf-8")
        ).hexdigest()
        if (
            observed_blob != new_blob
            or observed_content_digest != expected_content_digest
        ):
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="independent file postcondition does not match",
            )

        final_ref_readback = verification_backend.execute(
            _github_read_work(target, expected=False)
        )
        if not final_ref_readback.succeeded or len(final_ref_readback.outputs) != 1:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                reason="final independent branch readback is unavailable",
            )
        if final_ref_readback.outputs[0] != new_commit:
            return VerificationOutcome(
                work=attempt.work,
                status=WorkUnitStatus.SUPERSEDED,
                reason="mutated branch moved during independent verification",
            )

    if not lease_store.complete(attempt.lease, now=now):
        return VerificationOutcome(
            work=attempt.work,
            status=WorkUnitStatus.OUTCOME_UNKNOWN,
            reason="lease/fence no longer authorizes mutation completion",
        )

    return VerificationOutcome(
        work=attempt.work,
        status=WorkUnitStatus.COMPLETE,
        reason="mutation postcondition independently verified",
    )
