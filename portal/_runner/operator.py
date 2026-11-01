from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Callable, Iterable

from .budgets import BudgetEnvelope
from .durable_dispatch import (
    SqliteDispatchAdmissionStore,
    execute_admitted,
    verify_observed_attempt,
)
from .github_backend import (
    GitHubBackend,
    GitHubOperation,
    GitHubRestTransport,
    GitHubTransport,
    TargetAuthorityGrant,
)
from .m6_github import (
    GitHubCurrentSubjectReader,
    GitHubReadInspectionBackend,
    frontier_to_github_inspection_work,
)
from .models import ExactSubject, Frontier, FrontierStatus
from .persistent_state import SqliteLeaseStore
from .recovery import load_recovery_snapshots
from .recursive_state import SqliteRecursiveWorkStore
from .work_units import WorkUnitStatus, work_unit_fingerprint


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_TERMINAL = frozenset(
    {
        WorkUnitStatus.COMPLETE,
        WorkUnitStatus.FAILED_DETERMINISTIC,
        WorkUnitStatus.SUPERSEDED,
    }
)


@dataclass(frozen=True)
class InspectionRunResult:
    lineage_id: str
    work_fingerprint: str
    status: WorkUnitStatus
    reason: str
    backend_classification: str
    fencing_token: int
    budget_generation: int
    work_generation: int


def _require_exact_subject(subject: ExactSubject) -> None:
    if not subject.repository or "/" not in subject.repository:
        raise ValueError("inspection subject requires owner/repository")
    if not subject.ref:
        raise ValueError("inspection subject requires an exact ref")
    if subject.commit is None or _SHA40.fullmatch(subject.commit) is None:
        raise ValueError("inspection subject requires a lowercase 40-hex commit")


def build_github_read_backend(
    subjects: Iterable[ExactSubject],
    *,
    token: str | None,
    transport: GitHubTransport | None = None,
) -> GitHubBackend:
    unique_scopes: set[tuple[str, str]] = set()
    normalized = tuple(subjects)
    if not normalized:
        raise ValueError("inspection requires at least one exact subject")

    for subject in normalized:
        _require_exact_subject(subject)
        unique_scopes.add((subject.repository, subject.ref))

    grants = tuple(
        TargetAuthorityGrant(
            repository=repository,
            operations=(GitHubOperation.READ_REF,),
            ref_prefixes=(ref,),
        )
        for repository, ref in sorted(unique_scopes)
    )
    return GitHubBackend(
        transport=transport or GitHubRestTransport(token=token),
        route_capabilities=("github.read_ref",),
        grants=grants,
    )


def _inspection_evidence_valid(work, result) -> bool:
    expected_outputs = tuple("READ_REF_OK" for _ in work.inputs)
    return (
        result.succeeded
        and result.outputs == expected_outputs
        and result.evidence
        == (
            "m6:github-read-inspection",
            "github:readback-verified",
            f"subjects={len(work.inputs)}",
        )
    )


def run_durable_github_read_inspection(
    *,
    frontier: Frontier,
    target_subject: ExactSubject,
    state_db: Path,
    lineage_id: str,
    holder: str,
    lease_ttl: float,
    registry_digest: str,
    authorized_target_repositories: Iterable[str],
    authorized_provider_repositories: Iterable[str],
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> InspectionRunResult:
    """Execute one READY INSPECT frontier through the durable M6 path.

    This is intentionally read-only. It persists the root budget/work record,
    atomically admits execution, journals the backend result, independently
    re-reads exact GitHub currentness, and atomically finalizes terminal state.
    """
    if frontier.status is not FrontierStatus.READY:
        raise ValueError("operator execution requires a READY frontier")
    if frontier.work_type != "INSPECT":
        raise ValueError("operator execution currently supports INSPECT only")
    if not lineage_id.strip():
        raise ValueError("operator lineage id is required")
    if not holder.strip():
        raise ValueError("operator lease holder is required")
    if lease_ttl <= 0:
        raise ValueError("operator lease ttl must be positive")
    if (
        len(registry_digest) != 64
        or any(character not in "0123456789abcdef" for character in registry_digest)
    ):
        raise ValueError("project registry digest must be lowercase SHA-256")

    _require_exact_subject(frontier.subject)
    _require_exact_subject(target_subject)
    allowed_targets = frozenset(str(item) for item in authorized_target_repositories)
    allowed_providers = frozenset(
        str(item) for item in authorized_provider_repositories
    )
    if frontier.subject.repository not in allowed_providers:
        raise ValueError(
            "inspection provider repository is not registered in the current portfolio"
        )
    if target_subject.repository not in allowed_targets:
        raise ValueError(
            "inspection target repository is not authorized for the frontier project"
        )
    backend = build_github_read_backend(
        (frontier.subject, target_subject),
        token=token,
        transport=transport,
    )

    state_db = Path(state_db)
    state_db.parent.mkdir(parents=True, exist_ok=True)

    work = frontier_to_github_inspection_work(
        frontier,
        target_subject,
        depth=0,
        registry_digest=registry_digest,
    )
    fingerprint = work_unit_fingerprint(work)
    effective_capabilities = set(work.required_capabilities) | {"read", "analyze"}

    budget = BudgetEnvelope(
        lineage_id=lineage_id,
        max_depth=1,
        depth=0,
        remaining_children=0,
        remaining_active=2,
        remaining_retries=1,
        remaining_backend_jobs=2,
    )

    lease_store = SqliteLeaseStore(state_db)
    recursive_store = SqliteRecursiveWorkStore(state_db)
    dispatch_store = SqliteDispatchAdmissionStore(state_db)
    try:
        budget_generation, work_generation = dispatch_store.initialize_root(
            budget=budget,
            work=work,
            effective_capabilities=effective_capabilities,
        )

        admitted = dispatch_store.admit(
            lineage_id=lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=budget.scope_id,
            expected_budget_generation=budget_generation,
            expected_work_generation=work_generation,
            holder=holder,
            now=clock(),
            ttl=lease_ttl,
        )

        running = recursive_store.compare_and_swap_status(
            lineage_id=lineage_id,
            work_fingerprint_value=fingerprint,
            expected_generation=admitted.work_generation,
            status=WorkUnitStatus.RUNNING,
            lease=admitted.lease,
            now=clock(),
        )

        attempt = execute_admitted(
            frontier=frontier,
            admission=admitted,
            backend=GitHubReadInspectionBackend(backend),
            journal=dispatch_store,
            recorded_at=clock(),
        )

        verifying = recursive_store.compare_and_swap_status(
            lineage_id=lineage_id,
            work_fingerprint_value=fingerprint,
            expected_generation=running.generation,
            status=WorkUnitStatus.VERIFYING,
            lease=admitted.lease,
            now=clock(),
        )

        verified_at = clock()
        outcome = verify_observed_attempt(
            attempt,
            lease_store=lease_store,
            now=verified_at,
            current_subject_reader=GitHubCurrentSubjectReader(backend).read,
            evidence_verifier=_inspection_evidence_valid,
            manage_lease=False,
        )

        work_generation = verifying.generation
        if outcome.status in _TERMINAL:
            work_generation = dispatch_store.finalize_terminal_verification(
                lineage_id=lineage_id,
                work_fingerprint_value=fingerprint,
                fencing_token=admitted.lease.fencing_token,
                expected_work_generation=verifying.generation,
                lease=admitted.lease,
                status=outcome.status,
                reason=outcome.reason,
                verified_at=verified_at,
            )
        elif outcome.status is WorkUnitStatus.OUTCOME_UNKNOWN:
            dispatch_store.record_verification(
                lineage_id=lineage_id,
                work_fingerprint_value=fingerprint,
                fencing_token=admitted.lease.fencing_token,
                status=outcome.status,
                reason=outcome.reason,
                verified_at=verified_at,
            )
            unknown = recursive_store.compare_and_swap_status(
                lineage_id=lineage_id,
                work_fingerprint_value=fingerprint,
                expected_generation=verifying.generation,
                status=WorkUnitStatus.OUTCOME_UNKNOWN,
                lease=admitted.lease,
                now=verified_at,
            )
            work_generation = unknown.generation
        elif outcome.status is not WorkUnitStatus.VERIFYING:
            raise ValueError(
                "operator inspection returned an unsupported verification state"
            )

        return InspectionRunResult(
            lineage_id=lineage_id,
            work_fingerprint=fingerprint,
            status=outcome.status,
            reason=outcome.reason,
            backend_classification=attempt.result.classification,
            fencing_token=admitted.lease.fencing_token,
            budget_generation=admitted.budget_generation,
            work_generation=work_generation,
        )
    finally:
        dispatch_store.close()
        recursive_store.close()
        lease_store.close()


def summarize_recovery_state(
    state_db: Path,
    *,
    now: float | None = None,
    detailed: bool = False,
) -> dict[str, object]:
    state_db = Path(state_db)
    if not state_db.exists():
        return {
            "unresolved": 0,
            "live_fences": 0,
            "actions": {},
            "phases": {},
            **({"items": []} if detailed else {}),
        }

    snapshots = load_recovery_snapshots(
        state_db,
        now=time.time() if now is None else now,
    )
    action_counts = Counter(snapshot.action.value for snapshot in snapshots)
    phase_counts = Counter(snapshot.entry.phase for snapshot in snapshots)
    payload: dict[str, object] = {
        "unresolved": len(snapshots),
        "live_fences": sum(1 for snapshot in snapshots if snapshot.fence_live),
        "actions": dict(sorted(action_counts.items())),
        "phases": dict(sorted(phase_counts.items())),
    }
    if detailed:
        payload["items"] = [
            {
                "lineage_id": snapshot.entry.lineage_id,
                "work_fingerprint": snapshot.entry.work_fingerprint,
                "fencing_token": snapshot.entry.fencing_token,
                "phase": snapshot.entry.phase,
                "action": snapshot.action.value,
                "fence_current": snapshot.fence_current,
                "fence_live": snapshot.fence_live,
                "backend_reexecution_allowed": snapshot.backend_reexecution_allowed,
            }
            for snapshot in snapshots
        ]
    return payload
