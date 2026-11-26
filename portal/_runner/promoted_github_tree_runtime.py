from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable

from .durable_dispatch import SqliteDispatchAdmissionStore
from .execution_promotion import (
    SOURCE_WRITE,
    _read_durable_promotion,
    _read_execution_request,
)
from .github_backend import GitHubFileState, GitHubTransport
from .leases import Lease
from .promoted_github_tree import GitHubSourceTreeWriteRequest
from .work_units import WorkUnitStatus


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_FINAL_REASON = (
    "promoted GitHub source-tree effect independently verified "
    "at exact candidate commit"
)


@dataclass(frozen=True)
class GitHubSourceTreeFinalization:
    status: str
    reason: str
    candidate_commit_sha: str
    candidate_blob_shas: tuple[tuple[str, str], ...]
    work_generation: int
    verified_at: float
    finalization_replayed: bool


@dataclass(frozen=True)
class GitHubSourceTreeReconciliation:
    outcome: str
    reason: str
    evidence: tuple[str, ...]
    observed_head: str
    work_generation: int


def _lease(
    store: SqliteDispatchAdmissionStore,
    *,
    receipt,
) -> Lease:
    row = store.connection.execute(
        """
        SELECT holder, fencing_token, expires_at, completed
        FROM leases
        WHERE work_fingerprint = ?
        """,
        (receipt.work_fingerprint,),
    ).fetchone()
    if row is None:
        raise ValueError("source-tree execution lease is missing")
    if bool(row[3]):
        raise ValueError("source-tree execution lease is completed")
    if int(row[1]) != receipt.fencing_token:
        raise ValueError("source-tree execution fence is stale")
    if str(row[0]) != receipt.holder:
        raise ValueError("source-tree execution holder mismatch")
    return Lease(
        work_fingerprint=receipt.work_fingerprint,
        holder=receipt.holder,
        fencing_token=receipt.fencing_token,
        expires_at=float(row[2]),
    )


def _request(
    store: SqliteDispatchAdmissionStore,
    *,
    receipt,
) -> GitHubSourceTreeWriteRequest:
    payload, digest = _read_execution_request(
        store,
        lineage_id=receipt.lineage_id,
        work_fingerprint_value=receipt.work_fingerprint,
        fencing_token=receipt.fencing_token,
    )
    if payload is None or digest is None:
        raise ValueError("source-tree execution request is missing")
    if receipt.execution_request_sha256 != digest:
        raise ValueError("source-tree execution request/promotion mismatch")
    request = GitHubSourceTreeWriteRequest.from_mapping(payload)
    if (
        request.repository != receipt.repository
        or request.ref != receipt.ref
        or request.expected_head != receipt.exact_head
    ):
        raise ValueError("source-tree execution request diverges from promotion")
    return request


def _verify_candidate_snapshot(
    *,
    transport: GitHubTransport,
    repository: str,
    ref: str,
    candidate_commit: str,
    request: GitHubSourceTreeWriteRequest,
    candidate_blobs: tuple[str, ...] | None,
) -> tuple[tuple[str, str], ...]:
    before = transport.read_ref(repository, ref)
    if before != candidate_commit:
        raise ValueError("source-tree candidate commit is no longer current")

    observed_pairs: list[tuple[str, str]] = []
    for index, file in enumerate(request.files):
        observed: GitHubFileState | None = transport.read_file(
            repository,
            file.path,
            candidate_commit,
        )
        if observed is None:
            raise ValueError(
                f"source-tree candidate file is missing: {file.path}"
            )
        if observed.content != file.content:
            raise ValueError(
                f"source-tree candidate content mismatch: {file.path}"
            )
        if candidate_blobs is not None and observed.sha != candidate_blobs[index]:
            raise ValueError(
                f"source-tree candidate blob mismatch: {file.path}"
            )
        observed_pairs.append((file.path, observed.sha))

    after = transport.read_ref(repository, ref)
    if after != before:
        raise ValueError("source-tree ref moved during verification snapshot")
    return tuple(observed_pairs)


def finalize_github_source_tree_write(
    *,
    state_db,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    transport: GitHubTransport,
    clock: Callable[[], float] = time.time,
) -> GitHubSourceTreeFinalization:
    """Finalize successful or conclusively reconciled multi-file SOURCE_WRITE."""

    store = SqliteDispatchAdmissionStore(state_db)
    try:
        receipt = _read_durable_promotion(
            store,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        if receipt.effect_class != SOURCE_WRITE:
            raise ValueError("source-tree finalization requires SOURCE_WRITE")
        request = _request(store, receipt=receipt)

        result = store.load_result(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        if result is None:
            raise ValueError("source-tree finalization requires backend result")

        candidate_blobs: tuple[str, ...] | None
        if result.succeeded and result.classification == "SUCCEEDED":
            if len(result.outputs) != len(request.files) + 1:
                raise ValueError("source-tree successful result shape is invalid")
            candidate_commit = result.outputs[0]
            candidate_blobs = tuple(result.outputs[1:])
        elif (
            not result.succeeded
            and result.classification == "OUTCOME_UNKNOWN"
            and len(result.outputs) == len(request.files) + 1
        ):
            candidate_commit = result.outputs[0]
            candidate_blobs = tuple(result.outputs[1:])
            reconciliation = store.load_latest_reconciliation(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            if (
                reconciliation is None
                or reconciliation.outcome != "EFFECT_CONFIRMED"
            ):
                raise ValueError(
                    "source-tree unknown outcome requires EFFECT_CONFIRMED reconciliation"
                )
        else:
            raise ValueError(
                "source-tree finalization requires successful or confirmed-unknown result"
            )

        if _SHA40.fullmatch(candidate_commit) is None:
            raise ValueError("source-tree candidate commit is invalid")
        if candidate_blobs is not None and any(
            _SHA40.fullmatch(value) is None for value in candidate_blobs
        ):
            raise ValueError("source-tree candidate blob identifier is invalid")

        work_row = store.connection.execute(
            """
            SELECT status, generation
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (lineage_id, work_fingerprint_value),
        ).fetchone()
        if work_row is None:
            raise ValueError("source-tree recursive work is missing")
        status = WorkUnitStatus(str(work_row[0]))
        generation = int(work_row[1])

        if status is WorkUnitStatus.COMPLETE:
            verification = store.load_latest_verification(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            lease_row = store.connection.execute(
                """
                SELECT holder, fencing_token, completed
                FROM leases
                WHERE work_fingerprint = ?
                """,
                (work_fingerprint_value,),
            ).fetchone()
            if (
                generation != receipt.promoted_work_generation + 2
                or verification is None
                or verification.status is not WorkUnitStatus.COMPLETE
                or verification.reason != _FINAL_REASON
                or lease_row is None
                or str(lease_row[0]) != receipt.holder
                or int(lease_row[1]) != receipt.fencing_token
                or not bool(lease_row[2])
            ):
                raise ValueError(
                    "completed source-tree finalization does not match exact receipt"
                )
            pairs = tuple(
                (file.path, candidate_blobs[index])
                for index, file in enumerate(request.files)
            )
            return GitHubSourceTreeFinalization(
                status="COMPLETE",
                reason=verification.reason,
                candidate_commit_sha=candidate_commit,
                candidate_blob_shas=pairs,
                work_generation=generation,
                verified_at=verification.verified_at,
                finalization_replayed=True,
            )

        started_at = float(clock())
        lease = _lease(store, receipt=receipt)
        if lease.expires_at <= started_at:
            raise ValueError("source-tree finalization fence has expired")

        pairs = _verify_candidate_snapshot(
            transport=transport,
            repository=receipt.repository,
            ref=receipt.ref,
            candidate_commit=candidate_commit,
            request=request,
            candidate_blobs=candidate_blobs,
        )

        verification_started = float(clock())
        if lease.expires_at <= verification_started:
            raise ValueError(
                "source-tree finalization fence expired before VERIFYING"
            )
        if status is WorkUnitStatus.RUNNING:
            if generation != receipt.promoted_work_generation:
                raise ValueError("source-tree promoted work generation diverged")
            generation = store.begin_verification(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
                expected_work_generation=generation,
                lease=lease,
                started_at=verification_started,
            )
            status = WorkUnitStatus.VERIFYING
        elif status is not WorkUnitStatus.VERIFYING:
            raise ValueError(
                f"source-tree finalization cannot resume from {status.value}"
            )

        final_pairs = _verify_candidate_snapshot(
            transport=transport,
            repository=receipt.repository,
            ref=receipt.ref,
            candidate_commit=candidate_commit,
            request=request,
            candidate_blobs=tuple(value for _path, value in pairs),
        )
        if final_pairs != pairs:
            raise ValueError("source-tree candidate changed after VERIFYING")

        verified_at = float(clock())
        if lease.expires_at <= verified_at:
            raise ValueError(
                "source-tree finalization fence expired during verification"
            )
        generation = store.finalize_terminal_verification(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            expected_work_generation=generation,
            lease=lease,
            status=WorkUnitStatus.COMPLETE,
            reason=_FINAL_REASON,
            verified_at=verified_at,
        )
        return GitHubSourceTreeFinalization(
            status="COMPLETE",
            reason=_FINAL_REASON,
            candidate_commit_sha=candidate_commit,
            candidate_blob_shas=pairs,
            work_generation=generation,
            verified_at=verified_at,
            finalization_replayed=False,
        )
    finally:
        store.close()


def reconcile_github_source_tree_write_outcome_unknown(
    *,
    state_db,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    transport: GitHubTransport,
    reconciler: str,
    clock: Callable[[], float] = time.time,
) -> GitHubSourceTreeReconciliation:
    """Read-only reconciliation of one recorded ambiguous tree publication."""

    store = SqliteDispatchAdmissionStore(state_db)
    try:
        receipt = _read_durable_promotion(
            store,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        if receipt.effect_class != SOURCE_WRITE:
            raise ValueError("source-tree reconciliation requires SOURCE_WRITE")
        request = _request(store, receipt=receipt)
        result = store.load_result(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        if (
            result is None
            or result.succeeded
            or result.classification != "OUTCOME_UNKNOWN"
            or len(result.outputs) != len(request.files) + 1
        ):
            raise ValueError(
                "source-tree reconciliation requires recorded OUTCOME_UNKNOWN with exact candidate blobs"
            )
        candidate_commit = result.outputs[0]
        candidate_blobs = tuple(result.outputs[1:])
        if _SHA40.fullmatch(candidate_commit) is None:
            raise ValueError("source-tree candidate commit is invalid")

        observed_head = transport.read_ref(receipt.repository, receipt.ref)
        evidence: list[str] = [
            f"repository={receipt.repository}",
            f"ref={receipt.ref}",
            f"expected_head={receipt.exact_head}",
            f"candidate_commit={candidate_commit}",
            f"observed_head={observed_head}",
        ]

        complete = observed_head == candidate_commit
        if complete:
            for index, file in enumerate(request.files):
                observed = transport.read_file(
                    receipt.repository,
                    file.path,
                    candidate_commit,
                )
                if (
                    observed is None
                    or observed.content != file.content
                    or observed.sha != candidate_blobs[index]
                ):
                    complete = False
                    evidence.append(f"file_mismatch={file.path}")
                    break
                evidence.append(f"observed_blob:{file.path}={observed.sha}")

        observed_after = transport.read_ref(receipt.repository, receipt.ref)
        if observed_after != observed_head:
            complete = False
            reason = "repository ref moved during source-tree reconciliation"
        elif complete:
            reason = (
                "candidate commit and every proposed file are published "
                "at the exact ref"
            )
        else:
            reason = (
                "live GitHub state does not positively prove candidate "
                "source-tree publication"
            )

        outcome = "EFFECT_CONFIRMED" if complete else "INDETERMINATE"
        lease = _lease(store, receipt=receipt)
        generation = store.reconcile_admitted(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            expected_work_generation=receipt.promoted_work_generation,
            lease=lease,
            outcome=outcome,
            reason=reason,
            evidence=tuple(evidence),
            reconciler=reconciler,
            observed_at=float(clock()),
            allow_recorded_outcome_unknown=True,
        )
        return GitHubSourceTreeReconciliation(
            outcome=outcome,
            reason=reason,
            evidence=tuple(evidence),
            observed_head=observed_head,
            work_generation=generation,
        )
    finally:
        store.close()
