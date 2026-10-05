from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Mapping

from runner.github_backend import GitHubTransport

from .models import ExecutionNode
from .source_proposal import load_source_tree_proposal
from .wave_runtime import (
    PortalWaveStore,
    verify_portal_wave_delivery,
)
from .worker_backend import ProcessWorkerSpec, run_process_worker


@dataclass(frozen=True)
class PortalWorkerSlotResult:
    node_id: str
    slot: int
    holder: str
    subject_id: str | None
    claimed: bool
    state: str
    receipt_class: str | None
    reason: str


@dataclass(frozen=True)
class PortalWorkerPassResult:
    run_id: str
    slots: tuple[PortalWorkerSlotResult, ...]
    claimed: int
    verified_complete: int
    verified_held: int
    failed_or_unknown: int
    no_work: int
    awaiting_promotion: int = 0

    @property
    def progress_made(self) -> bool:
        return self.claimed > 0


def _error_digest(
    *,
    run_id: str,
    node_id: str,
    subject_id: str,
    error: Exception,
) -> str:
    payload = {
        "schema": "PORTAL_WORKER_PROTOCOL_ERROR_V1",
        "run_id": run_id,
        "node_id": node_id,
        "subject_id": subject_id,
        "error_type": type(error).__name__,
        "error": str(error),
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run_slot(
    *,
    state_db: Path,
    run_id: str,
    node_id: str,
    slot: int,
    backend: ProcessWorkerSpec,
    workspace_root: Path,
    holder_prefix: str,
    delivery_lease_ttl: float,
    verifier: str,
    token: str | None,
    transport: GitHubTransport | None,
    clock: Callable[[], float],
    claim_attempts: int,
) -> PortalWorkerSlotResult:
    holder = f"{holder_prefix}:{node_id}:{slot}"
    claim = None
    for attempt in range(claim_attempts):
        store = PortalWaveStore(state_db)
        try:
            claim = store.claim_delivery(
                run_id=run_id,
                node_id=node_id,
                holder=holder,
                now=float(clock()),
                ttl=delivery_lease_ttl,
            )
        finally:
            store.close()
        if claim is not None:
            break
        if attempt + 1 < claim_attempts:
            time.sleep(0.005)

    if claim is None:
        return PortalWorkerSlotResult(
            node_id=node_id,
            slot=slot,
            holder=holder,
            subject_id=None,
            claimed=False,
            state="NO_WORK",
            receipt_class=None,
            reason="no pending delivery for node",
        )

    try:
        worker = run_process_worker(
            packet=claim.payload,
            spec=backend,
            workspace_root=workspace_root,
        )
        receipt_class = worker.receipt_class
        evidence_sha256 = worker.evidence_sha256
        reason = worker.reason
    except ValueError as exc:
        receipt_class = "OUTCOME_UNKNOWN"
        evidence_sha256 = _error_digest(
            run_id=run_id,
            node_id=node_id,
            subject_id=claim.subject_id,
            error=exc,
        )
        reason = (
            "worker process returned an invalid/unverifiable protocol result; "
            f"outcome is unknown: {type(exc).__name__}: {exc}"
        )
    except Exception as exc:
        receipt_class = "OUTCOME_UNKNOWN"
        evidence_sha256 = _error_digest(
            run_id=run_id,
            node_id=node_id,
            subject_id=claim.subject_id,
            error=exc,
        )
        reason = (
            "worker backend failed with unknown outcome: "
            f"{type(exc).__name__}: {exc}"
        )

    store = PortalWaveStore(state_db)
    try:
        receipt = store.record_delivery_receipt(
            run_id=run_id,
            subject_id=claim.subject_id,
            node_id=node_id,
            holder=holder,
            expected_fencing_token=claim.fencing_token,
            receipt_class=receipt_class,
            result_repository=None,
            result_ref=None,
            result_head=None,
            evidence_sha256=evidence_sha256,
            reason=reason,
            now=float(clock()),
        )
    finally:
        store.close()

    state = receipt.state
    final_reason = reason
    if receipt.state == "RECEIPT_RECORDED":
        verification = verify_portal_wave_delivery(
            state_db=state_db,
            run_id=run_id,
            subject_id=claim.subject_id,
            verifier=verifier,
            token=token,
            transport=transport,
            clock=clock,
        )
        state = verification.state
        final_reason = verification.reason

    return PortalWorkerSlotResult(
        node_id=node_id,
        slot=slot,
        holder=holder,
        subject_id=claim.subject_id,
        claimed=True,
        state=state,
        receipt_class=receipt_class,
        reason=final_reason,
    )


def run_wave_workers_once(
    *,
    state_db: Path,
    run_id: str,
    nodes: tuple[ExecutionNode, ...],
    backends: Mapping[str, ProcessWorkerSpec],
    workspace_root: Path,
    holder_prefix: str,
    delivery_lease_ttl: float,
    verifier: str,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalWorkerPassResult:
    """Fill each enabled node's declared worker capacity once.

    Every slot independently obtains one durable delivery fence before invoking
    its backend. Successful worker receipts are independently verified before
    they count as complete.
    """

    if not holder_prefix.strip():
        raise ValueError("worker holder_prefix is required")
    if not verifier.strip():
        raise ValueError("worker verifier is required")
    if delivery_lease_ttl <= 0:
        raise ValueError("delivery_lease_ttl must be positive")

    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("duplicate execution node id")

    enabled = tuple(
        sorted(
            (node for node in nodes if node.enabled),
            key=lambda node: node.node_id,
        )
    )
    for node in enabled:
        if node.node_id not in backends:
            raise ValueError(
                f"missing worker backend for enabled node: {node.node_id}"
            )

    slots = tuple(
        (node.node_id, slot)
        for node in enabled
        for slot in range(node.max_parallel)
    )
    if not slots:
        raise ValueError("no enabled worker execution slots")

    results: list[PortalWorkerSlotResult] = []
    with ThreadPoolExecutor(
        max_workers=len(slots),
        thread_name_prefix="portal-worker",
    ) as executor:
        futures = [
            executor.submit(
                _run_slot,
                state_db=Path(state_db),
                run_id=run_id,
                node_id=node_id,
                slot=slot,
                backend=backends[node_id],
                workspace_root=Path(workspace_root),
                holder_prefix=holder_prefix,
                delivery_lease_ttl=delivery_lease_ttl,
                verifier=verifier,
                token=token,
                transport=transport,
                clock=clock,
                claim_attempts=len(slots) + 1,
            )
            for node_id, slot in slots
        ]
        for future in as_completed(futures):
            results.append(future.result())

    ordered = tuple(sorted(results, key=lambda item: (item.node_id, item.slot)))
    claimed = sum(item.claimed for item in ordered)
    verified_complete = sum(
        item.state == "VERIFIED_COMPLETE" for item in ordered
    )
    verified_held = sum(item.state == "VERIFIED_HELD" for item in ordered)
    no_work = sum(item.state == "NO_WORK" for item in ordered)
    failed_or_unknown = sum(
        item.state
        in {
            "FAILED_RETRYABLE",
            "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN",
            "VERIFICATION_STALE",
        }
        for item in ordered
    )
    return PortalWorkerPassResult(
        run_id=run_id,
        slots=ordered,
        claimed=claimed,
        verified_complete=verified_complete,
        verified_held=verified_held,
        failed_or_unknown=failed_or_unknown,
        no_work=no_work,
    )



def _run_proposal_slot(
    *,
    state_db: Path,
    run_id: str,
    node_id: str,
    slot: int,
    backend: ProcessWorkerSpec,
    workspace_root: Path,
    holder_prefix: str,
    delivery_lease_ttl: float,
    token: str | None,
    transport: GitHubTransport | None,
    clock: Callable[[], float],
    claim_attempts: int,
) -> PortalWorkerSlotResult:
    holder = f"{holder_prefix}:{node_id}:{slot}"
    claim = None
    for attempt in range(claim_attempts):
        store = PortalWaveStore(state_db)
        try:
            claim = store.claim_advisory_delivery(
                run_id=run_id,
                node_id=node_id,
                holder=holder,
                now=float(clock()),
                ttl=delivery_lease_ttl,
            )
        finally:
            store.close()
        if claim is not None:
            break
        if attempt + 1 < claim_attempts:
            time.sleep(0.005)

    if claim is None:
        return PortalWorkerSlotResult(
            node_id=node_id,
            slot=slot,
            holder=holder,
            subject_id=None,
            claimed=False,
            state="NO_WORK",
            receipt_class=None,
            reason="no pending advisory delivery for node",
        )

    worker = None
    try:
        worker = run_process_worker(
            packet=claim.payload,
            spec=backend,
            workspace_root=workspace_root,
        )
        if worker.receipt_class != "PROPOSED_SOURCE_TREE":
            raise ValueError(
                "advisory source worker must return PROPOSED_SOURCE_TREE"
            )
        proposal_artifacts = tuple(
            artifact
            for artifact in worker.artifacts
            if artifact.kind == "SOURCE_TREE_PROPOSAL"
        )
        if len(proposal_artifacts) != 1:
            raise ValueError(
                "PROPOSED_SOURCE_TREE requires exactly one SOURCE_TREE_PROPOSAL artifact"
            )
        proposal = load_source_tree_proposal(
            proposal_artifacts[0].path,
            packet=claim.payload,
        )
        receipt_class = worker.receipt_class
        evidence_sha256 = worker.evidence_sha256
        reason = worker.reason
    except ValueError as exc:
        receipt_class = "FAILED_DETERMINISTIC"
        evidence_sha256 = _error_digest(
            run_id=run_id,
            node_id=node_id,
            subject_id=claim.subject_id,
            error=exc,
        )
        reason = (
            "advisory proposal protocol rejected deterministically: "
            f"{type(exc).__name__}: {exc}"
        )
        proposal = None
    except Exception as exc:
        receipt_class = "OUTCOME_UNKNOWN"
        evidence_sha256 = _error_digest(
            run_id=run_id,
            node_id=node_id,
            subject_id=claim.subject_id,
            error=exc,
        )
        reason = (
            "advisory proposal backend failed with unknown outcome: "
            f"{type(exc).__name__}: {exc}"
        )
        proposal = None

    store = PortalWaveStore(state_db)
    try:
        receipt = store.record_delivery_receipt(
            run_id=run_id,
            subject_id=claim.subject_id,
            node_id=node_id,
            holder=holder,
            expected_fencing_token=claim.fencing_token,
            receipt_class=receipt_class,
            result_repository=None,
            result_ref=None,
            result_head=None,
            evidence_sha256=evidence_sha256,
            reason=reason,
            now=float(clock()),
        )
        if proposal is not None and receipt.state == "RECEIPT_RECORDED":
            persisted = store.record_source_proposal(
                run_id=run_id,
                subject_id=claim.subject_id,
                proposal=proposal,
                now=float(clock()),
            )
            state = str(persisted["state"])
            final_reason = (
                "exact source-tree proposal validated and persisted; "
                "awaiting Project Runner promotion"
            )
        else:
            state = receipt.state
            final_reason = reason
    finally:
        store.close()

    return PortalWorkerSlotResult(
        node_id=node_id,
        slot=slot,
        holder=holder,
        subject_id=claim.subject_id,
        claimed=True,
        state=state,
        receipt_class=receipt_class,
        reason=final_reason,
    )


def run_wave_proposal_workers_once(
    *,
    state_db: Path,
    run_id: str,
    nodes: tuple[ExecutionNode, ...],
    backends: Mapping[str, ProcessWorkerSpec],
    workspace_root: Path,
    holder_prefix: str,
    delivery_lease_ttl: float,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalWorkerPassResult:
    """Run one parallel advisory proposal pass without execution authority."""

    if not holder_prefix.strip():
        raise ValueError("worker holder_prefix is required")
    if delivery_lease_ttl <= 0:
        raise ValueError("delivery_lease_ttl must be positive")

    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("duplicate execution node id")
    enabled = tuple(
        sorted(
            (node for node in nodes if node.enabled),
            key=lambda node: node.node_id,
        )
    )
    for node in enabled:
        if node.node_id not in backends:
            raise ValueError(
                f"missing worker backend for enabled node: {node.node_id}"
            )
    slots = tuple(
        (node.node_id, slot)
        for node in enabled
        for slot in range(node.max_parallel)
    )
    if not slots:
        raise ValueError("no enabled worker execution slots")

    results: list[PortalWorkerSlotResult] = []
    with ThreadPoolExecutor(
        max_workers=len(slots),
        thread_name_prefix="portal-proposal",
    ) as executor:
        futures = [
            executor.submit(
                _run_proposal_slot,
                state_db=Path(state_db),
                run_id=run_id,
                node_id=node_id,
                slot=slot,
                backend=backends[node_id],
                workspace_root=Path(workspace_root),
                holder_prefix=holder_prefix,
                delivery_lease_ttl=delivery_lease_ttl,
                token=token,
                transport=transport,
                clock=clock,
                claim_attempts=len(slots) + 1,
            )
            for node_id, slot in slots
        ]
        for future in as_completed(futures):
            results.append(future.result())

    ordered = tuple(sorted(results, key=lambda item: (item.node_id, item.slot)))
    awaiting_promotion = sum(
        item.state == "AWAITING_PROMOTION" for item in ordered
    )
    no_work = sum(item.state == "NO_WORK" for item in ordered)
    failed_or_unknown = sum(
        item.state
        in {
            "FAILED_RETRYABLE",
            "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN",
            "VERIFICATION_STALE",
        }
        for item in ordered
    )
    return PortalWorkerPassResult(
        run_id=run_id,
        slots=ordered,
        claimed=sum(item.claimed for item in ordered),
        verified_complete=0,
        verified_held=0,
        failed_or_unknown=failed_or_unknown,
        no_work=no_work,
        awaiting_promotion=awaiting_promotion,
    )
