from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Iterable, Mapping

from .github_backend import GitHubRestTransport, GitHubTransport
from .models import Frontier, InvocationRoute, WorkerDefinition
from .worker_routing import SqliteWorkerRouteStore


REFERENCE_WORKER_ID = "project-runner-reference-read-worker"
REFERENCE_WORKER_ROUTE = InvocationRoute.RUNNER_ACTION_PULL


@dataclass(frozen=True)
class ReferenceWorkerResult:
    claimed: bool
    route_id: str | None
    delivery_fencing_token: int | None
    receipt_class: str | None
    receipt_sha256: str | None
    reason: str


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"worker packet requires {key}")
    return value


def run_reference_read_worker_once(
    *,
    state_db: Path,
    workers: Iterable[WorkerDefinition],
    worker_registry_digest: str,
    holder: str,
    lease_ttl: float = 300.0,
    token: str | None = None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> ReferenceWorkerResult:
    worker_tuple = tuple(workers)
    store = SqliteWorkerRouteStore(state_db)
    try:
        claim = store.claim_next(
            worker_id=REFERENCE_WORKER_ID,
            invocation_route=REFERENCE_WORKER_ROUTE,
            workers=worker_tuple,
            holder=holder,
            now=clock(),
            ttl=lease_ttl,
            worker_registry_digest=worker_registry_digest,
            allow_private=False,
        )
        if claim is None:
            return ReferenceWorkerResult(
                claimed=False,
                route_id=None,
                delivery_fencing_token=None,
                receipt_class=None,
                receipt_sha256=None,
                reason="no qualified reference-worker route is pending",
            )

        packet = claim.payload
        frontier_raw = packet.get("frontier")
        if not isinstance(frontier_raw, Mapping):
            raise ValueError("worker packet requires frontier mapping")
        frontier = Frontier.from_mapping(frontier_raw)
        if frontier.subject.commit is None:
            raise ValueError("reference worker requires exact provider commit")

        target_repository = _required_text(packet, "target_repository")
        target_ref = _required_text(packet, "target_ref")
        target_head = _required_text(packet, "target_head")

        live_transport = transport or GitHubRestTransport(token=token)
        receipt_class = "FAILED_RETRYABLE"
        observed_provider: str | None = None
        observed_target: str | None = None
        transport_state = "READ_FAILED"
        try:
            observed_provider = live_transport.read_ref(
                frontier.subject.repository,
                frontier.subject.ref,
            )
            observed_target = live_transport.read_ref(
                target_repository,
                target_ref,
            )
            transport_state = "READ_SUCCEEDED"
            if (
                observed_provider == frontier.subject.commit
                and observed_target == target_head
            ):
                receipt_class = "SUCCEEDED"
            else:
                receipt_class = "SUPERSEDED"
        except (KeyError, RuntimeError, ValueError):
            receipt_class = "FAILED_RETRYABLE"

        evidence = {
            "route_id": claim.route_id,
            "worker_id": REFERENCE_WORKER_ID,
            "invocation_route": REFERENCE_WORKER_ROUTE.value,
            "delivery_fencing_token": claim.fencing_token,
            "provider_expected": frontier.subject.commit,
            "provider_observed": observed_provider,
            "target_expected": target_head,
            "target_observed": observed_target,
            "transport_state": transport_state,
            "receipt_class": receipt_class,
        }
        canonical = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        receipt = store.record_receipt(
            route_id=claim.route_id,
            worker_id=REFERENCE_WORKER_ID,
            invocation_route=REFERENCE_WORKER_ROUTE,
            holder=holder,
            expected_fencing_token=claim.fencing_token,
            receipt_class=receipt_class,
            receipt_sha256=receipt_sha256,
            now=clock(),
        )
        return ReferenceWorkerResult(
            claimed=True,
            route_id=claim.route_id,
            delivery_fencing_token=claim.fencing_token,
            receipt_class=receipt.receipt_class,
            receipt_sha256=receipt.receipt_sha256,
            reason="reference worker recorded a fenced read-only receipt",
        )
    finally:
        store.close()
