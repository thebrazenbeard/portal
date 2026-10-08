from __future__ import annotations

from pathlib import Path
import time
from typing import Callable, Mapping, TYPE_CHECKING

from runner.execution_promotion import NO_PROTECTED_EFFECT
from runner.github_backend import GitHubTransport

from .adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from .execution_router import CapabilityExecutionAdapter
from .models import ExecutionNode
from .route_resolver import PortalRouteAdvertisement, PortalRouteRequest
from .wave_runtime import PortalWaveStore
from .worker_backend import (
    ProcessWorkerSpec,
    validate_process_worker_workspace_locality,
)
from .worker_runtime import run_wave_proposal_workers_once

if TYPE_CHECKING:
    from .session import PortalSessionResult


_ADAPTER_ID = "process-proposal"
_PROPOSAL_HELD_STATES = frozenset(
    {
        "AWAITING_PROMOTION",
        "FAILED_PRECONDITION",
        "FAILED_EXECUTION",
        "FAILED_DETERMINISTIC",
        "FAILED_RETRYABLE",
    }
)


class PortalProposalProcessAdapter:
    """Advisory process-worker adapter for one Portal command session.

    This adapter deliberately has no protected-effect authority. It consumes the
    existing advisory worker runtime, persists source-tree proposals through the
    owning wave store, and reports those subjects as verified held while they
    await a separate promotion/authority decision.
    """

    adapter_id = _ADAPTER_ID

    def __init__(
        self,
        *,
        state_db: Path,
        nodes: tuple[ExecutionNode, ...],
        backends: Mapping[str, ProcessWorkerSpec],
        workspace_root: Path,
        holder_prefix: str,
        delivery_lease_ttl: float,
        token: str | None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not holder_prefix.strip():
            raise ValueError("process proposal holder_prefix is required")
        if delivery_lease_ttl <= 0:
            raise ValueError("process proposal delivery_lease_ttl must be positive")
        node_ids = [node.node_id for node in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate execution node id")

        # Fail before durable session placement/dispatch: all attached local
        # process workers share this workspace root.
        for spec in backends.values():
            validate_process_worker_workspace_locality(
                workspace_root,
                allow_cross_os_workspace=spec.allow_cross_os_workspace,
            )
        self.state_db = Path(state_db)
        self.nodes = tuple(nodes)
        self.backends = dict(backends)
        self.workspace_root = Path(workspace_root)
        self.holder_prefix = holder_prefix.strip()
        self.delivery_lease_ttl = float(delivery_lease_ttl)
        self.token = token
        self.transport = transport
        self.clock = clock
        self._node_ids = frozenset(node_ids)

    @staticmethod
    def _route_id(node_id: str) -> str:
        return f"{_ADAPTER_ID}:{node_id}"

    def select_routes(
        self,
        result: "PortalSessionResult",
    ) -> tuple[PortalRouteBinding, ...]:
        bindings: list[PortalRouteBinding] = []
        for packet in result.packets:
            if packet.node_id not in self._node_ids:
                raise ValueError(
                    "process proposal packet references unknown execution node"
                )
            if packet.node_id not in self.backends:
                raise ValueError(
                    "missing process proposal backend for assigned node: "
                    + packet.node_id
                )
            bindings.append(
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id=packet.subject_id,
                    adapter_id=_ADAPTER_ID,
                    route_id=self._route_id(packet.node_id),
                )
            )
        return tuple(bindings)

    @staticmethod
    def _delivery_evidence(record: Mapping[str, object]) -> str | None:
        digest = record.get("evidence_sha256") or record.get("receipt_sha256")
        if isinstance(digest, str) and digest:
            return "delivery:" + digest
        return None

    def dispatch(
        self,
        result: "PortalSessionResult",
        routes: tuple[PortalRouteBinding, ...],
    ) -> tuple[PortalDispatchRecord, ...]:
        if not routes:
            return ()

        packets_by_subject = {
            packet.subject_id: packet
            for packet in result.packets
        }
        allowed_subject_ids: list[str] = []
        seen_subjects: set[str] = set()
        for binding in routes:
            if binding.adapter_id != _ADAPTER_ID:
                raise ValueError("process proposal route uses wrong adapter_id")
            if binding.subject_id in seen_subjects:
                raise ValueError("duplicate process proposal route subject")
            seen_subjects.add(binding.subject_id)

            packet = packets_by_subject.get(binding.subject_id)
            if packet is None:
                raise ValueError(
                    "process proposal route references non-admitted subject"
                )
            if binding.route_id != self._route_id(packet.node_id):
                raise ValueError(
                    "process proposal route does not match assigned node"
                )
            if packet.node_id not in self._node_ids:
                raise ValueError(
                    "process proposal packet references unknown execution node"
                )
            if packet.node_id not in self.backends:
                raise ValueError(
                    "missing process proposal backend for assigned node: "
                    + packet.node_id
                )
            allowed_subject_ids.append(binding.subject_id)

        worker_pass = run_wave_proposal_workers_once(
            state_db=self.state_db,
            run_id=result.wave_run_id,
            nodes=self.nodes,
            backends=self.backends,
            workspace_root=self.workspace_root,
            holder_prefix=self.holder_prefix,
            delivery_lease_ttl=self.delivery_lease_ttl,
            token=self.token,
            allowed_subject_ids=tuple(allowed_subject_ids),
            transport=self.transport,
            clock=self.clock,
        )
        by_subject = {
            slot.subject_id: slot
            for slot in worker_pass.slots
            if slot.claimed and slot.subject_id is not None
        }

        store = PortalWaveStore(self.state_db)
        records: list[PortalDispatchRecord] = []
        try:
            for binding in routes:
                slot = by_subject.get(binding.subject_id)
                if slot is None:
                    raise ValueError(
                        "process proposal worker did not claim bound subject"
                    )

                evidence_id: str | None = None
                state = slot.state
                if slot.state == "AWAITING_PROMOTION":
                    proposal = store.load_source_proposal(
                        run_id=result.wave_run_id,
                        subject_id=binding.subject_id,
                    )
                    digest = proposal.get("execution_request_sha256")
                    if not isinstance(digest, str) or not digest:
                        raise ValueError(
                            "process proposal is missing execution request digest"
                        )
                    evidence_id = "proposal:" + digest
                    state = "PROPOSAL_READY"
                else:
                    delivery = store.load_delivery(
                        run_id=result.wave_run_id,
                        subject_id=binding.subject_id,
                    )
                    evidence_id = self._delivery_evidence(delivery)

                records.append(
                    PortalDispatchRecord(
                        subject_kind=binding.subject_kind,
                        subject_id=binding.subject_id,
                        adapter_id=binding.adapter_id,
                        route_id=binding.route_id,
                        state=state,
                        evidence_id=evidence_id,
                    )
                )
        finally:
            store.close()
        return tuple(records)

    def reconcile(
        self,
        status: Mapping[str, object],
        subjects: tuple[Mapping[str, object], ...] | None = None,
    ) -> tuple[PortalReconciliationRecord, ...]:
        raw_subjects = status.get("subjects")
        if not isinstance(raw_subjects, list):
            raise ValueError("Portal status subjects must be a list")

        if subjects is None:
            candidates: tuple[Mapping[str, object], ...] = tuple(
                raw
                for raw in raw_subjects
                if isinstance(raw, Mapping)
            )
        else:
            candidates = tuple(subjects)
            status_bindings = {
                (
                    raw.get("subject_kind"),
                    raw.get("subject_id"),
                    raw.get("adapter_id"),
                    raw.get("route_id"),
                    raw.get("state"),
                )
                for raw in raw_subjects
                if isinstance(raw, Mapping)
            }
            for raw in candidates:
                if not isinstance(raw, Mapping):
                    raise ValueError(
                        "process proposal reconciliation subject must be a mapping"
                    )
                binding = (
                    raw.get("subject_kind"),
                    raw.get("subject_id"),
                    raw.get("adapter_id"),
                    raw.get("route_id"),
                    raw.get("state"),
                )
                if binding not in status_bindings:
                    raise ValueError(
                        "process proposal reconciliation subject is not in status"
                    )
                if raw.get("state") != "ACTIVE":
                    raise ValueError(
                        "process proposal reconciliation requires active subjects"
                    )
                if raw.get("adapter_id") != _ADAPTER_ID:
                    raise ValueError(
                        "process proposal reconciliation received foreign adapter subject"
                    )

        store = PortalWaveStore(self.state_db)
        records: list[PortalReconciliationRecord] = []
        try:
            for raw in candidates:
                if not isinstance(raw, Mapping):
                    raise ValueError("Portal status subject must be a mapping")
                if raw.get("state") != "ACTIVE":
                    continue
                if raw.get("adapter_id") != _ADAPTER_ID:
                    continue

                subject_kind = raw.get("subject_kind")
                subject_id = raw.get("subject_id")
                route_id = raw.get("route_id")
                wave_run_id = raw.get("wave_run_id")
                if not all(
                    isinstance(value, str) and value
                    for value in (
                        subject_kind,
                        subject_id,
                        route_id,
                        wave_run_id,
                    )
                ):
                    raise ValueError(
                        "process proposal subject has incomplete durable binding"
                    )

                proposal: Mapping[str, object] | None
                try:
                    proposal = store.load_source_proposal(
                        run_id=wave_run_id,
                        subject_id=subject_id,
                    )
                except ValueError:
                    proposal = None

                if proposal is not None:
                    proposal_state = str(proposal.get("state"))
                    digest = proposal.get("execution_request_sha256")
                    if not isinstance(digest, str) or not digest:
                        raise ValueError(
                            "process proposal is missing execution request digest"
                        )
                    evidence_id = "proposal:" + digest

                    if proposal_state == "VERIFIED_COMPLETE":
                        reconciliation_state = "VERIFIED_COMPLETE"
                    elif proposal_state == "OUTCOME_UNKNOWN":
                        reconciliation_state = "OUTCOME_UNKNOWN"
                    elif proposal_state in _PROPOSAL_HELD_STATES:
                        reconciliation_state = "VERIFIED_HELD"
                    else:
                        reconciliation_state = "IN_PROGRESS"

                    records.append(
                        PortalReconciliationRecord(
                            subject_kind=subject_kind,
                            subject_id=subject_id,
                            adapter_id=_ADAPTER_ID,
                            route_id=route_id,
                            state=reconciliation_state,
                            evidence_id=evidence_id,
                        )
                    )
                    continue

                delivery = store.load_delivery(
                    run_id=wave_run_id,
                    subject_id=subject_id,
                )
                delivery_state = str(delivery.get("state"))
                evidence_id = self._delivery_evidence(delivery)
                if evidence_id is None:
                    continue

                if delivery_state == "OUTCOME_UNKNOWN":
                    reconciliation_state = "OUTCOME_UNKNOWN"
                elif delivery_state == "VERIFIED_COMPLETE":
                    reconciliation_state = "VERIFIED_COMPLETE"
                elif delivery_state == "VERIFIED_HELD":
                    reconciliation_state = "VERIFIED_HELD"
                elif str(delivery.get("receipt_class")) in {
                    "FAILED_RETRYABLE",
                    "FAILED_DETERMINISTIC",
                }:
                    reconciliation_state = "VERIFIED_HELD"
                else:
                    reconciliation_state = "IN_PROGRESS"

                records.append(
                    PortalReconciliationRecord(
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        adapter_id=_ADAPTER_ID,
                        route_id=route_id,
                        state=reconciliation_state,
                        evidence_id=evidence_id,
                    )
                )
        finally:
            store.close()
        return tuple(records)

def build_process_proposal_execution_adapter(
    driver: PortalProposalProcessAdapter,
) -> CapabilityExecutionAdapter:
    """Wrap the local advisory process driver in capability-based routing."""

    nodes_by_id = {
        node.node_id: node
        for node in driver.nodes
    }

    def advertisement_provider(
        result: "PortalSessionResult",
    ) -> tuple[PortalRouteAdvertisement, ...]:
        advertisements: list[PortalRouteAdvertisement] = []
        for packet in result.packets:
            node = nodes_by_id.get(packet.node_id)
            if node is None:
                continue
            advertisements.append(
                PortalRouteAdvertisement(
                    adapter_id=driver.adapter_id,
                    route_id=driver._route_id(packet.node_id),
                    node_id=packet.node_id,
                    target_kind="repository",
                    target_id=packet.repository,
                    capabilities=("source_proposal",),
                    effect_capabilities=(NO_PROTECTED_EFFECT,),
                    authorized_effects=(NO_PROTECTED_EFFECT,),
                    available=(
                        node.enabled
                        and packet.node_id in driver.backends
                    ),
                    attached=True,
                    current=True,
                    preference=0,
                )
            )
        return tuple(advertisements)

    def request_builder(packet) -> PortalRouteRequest:
        return PortalRouteRequest(
            subject_kind="repository",
            subject_id=packet.subject_id,
            node_id=packet.node_id,
            target_kind="repository",
            target_id=packet.repository,
            required_capabilities=("source_proposal",),
            required_effect=NO_PROTECTED_EFFECT,
        )

    return CapabilityExecutionAdapter(
        advertisement_provider=advertisement_provider,
        request_builder=request_builder,
        drivers={driver.adapter_id: driver},
    )

