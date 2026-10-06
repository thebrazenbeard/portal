from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping
import uuid

from .host_bridge import PortalHostBridgeStore


_ALLOWED_RESULT_STATES = frozenset(
    {
        "IN_PROGRESS",
        "OUTCOME_UNKNOWN",
        "VERIFIED_COMPLETE",
        "VERIFIED_HELD",
    }
)


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


@dataclass(frozen=True)
class PortalHostDriverResult:
    state: str
    evidence_id: str

    def __post_init__(self) -> None:
        state = _required(self.state, "host driver result state")
        if state not in _ALLOWED_RESULT_STATES:
            raise ValueError("unsupported host driver result state")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "evidence_id",
            _required(self.evidence_id, "host driver evidence_id"),
        )


@dataclass(frozen=True)
class PortalHostPumpItemResult:
    dispatch_id: str
    adapter_id: str
    route_id: str
    state: str
    evidence_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PortalHostPumpResult:
    items: tuple[PortalHostPumpItemResult, ...]
    attempted: int
    verified_complete: int
    verified_held: int
    in_progress: int
    outcome_unknown: int
    no_driver: int


class PortalHostPump:
    """Consume already-bound host dispatches without becoming a scheduler.

    Route selection, admission, placement and authority decisions happen before
    this class runs. The pump only crosses the durable attempt boundary for an
    already-queued dispatch, delegates to the bound adapter driver, and records
    owning-driver reconciliation evidence.

    Once the attempt marker is durable, any driver exception is treated as an
    ambiguous effect. The dispatch stays ATTEMPTED and unresolved so a later
    host can reconcile it; it is never replayed by this pump.
    """

    def __init__(
        self,
        *,
        store: PortalHostBridgeStore,
        drivers: Mapping[str, object],
        attempt_id_factory: Callable[[Mapping[str, object]], str] | None = None,
    ) -> None:
        self.store = store
        self.drivers = dict(drivers)
        self.attempt_id_factory = (
            attempt_id_factory
            if attempt_id_factory is not None
            else lambda _dispatch: "portal-" + uuid.uuid4().hex
        )

    @staticmethod
    def _attempt_evidence_id(attempt_id: str) -> str:
        return "portal-host-attempt:" + attempt_id

    def run_once(
        self,
        *,
        session_id: str | None = None,
        max_dispatches: int | None = None,
    ) -> PortalHostPumpResult:
        if max_dispatches is not None:
            if type(max_dispatches) is not int or max_dispatches < 1:
                raise ValueError("max_dispatches must be a positive integer")

        pending = tuple(self.store.pending_dispatches(session_id=session_id))
        if max_dispatches is not None:
            pending = pending[:max_dispatches]

        items: list[PortalHostPumpItemResult] = []
        attempted = 0
        verified_complete = 0
        verified_held = 0
        in_progress = 0
        outcome_unknown = 0
        no_driver = 0

        for dispatch in pending:
            dispatch_id = _required(
                str(dispatch.get("dispatch_id") or ""),
                "dispatch_id",
            )
            adapter_id = _required(
                str(dispatch.get("adapter_id") or ""),
                "adapter_id",
            )
            route_id = _required(
                str(dispatch.get("route_id") or ""),
                "route_id",
            )

            driver = self.drivers.get(adapter_id)
            if driver is None:
                no_driver += 1
                items.append(
                    PortalHostPumpItemResult(
                        dispatch_id=dispatch_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state="NO_DRIVER",
                        reason="no driver registered for bound adapter",
                    )
                )
                continue

            attempt_id = _required(
                self.attempt_id_factory(dispatch),
                "attempt_id",
            )
            attempt_evidence = self._attempt_evidence_id(attempt_id)

            self.store.mark_attempted(
                dispatch_id=dispatch_id,
                attempt_id=attempt_id,
                evidence_id=attempt_evidence,
            )
            attempted += 1

            execute = getattr(driver, "execute", None)
            if execute is None or not callable(execute):
                outcome_unknown += 1
                items.append(
                    PortalHostPumpItemResult(
                        dispatch_id=dispatch_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state="OUTCOME_UNKNOWN",
                        evidence_id=attempt_evidence,
                        reason="bound driver has no callable execute method",
                    )
                )
                continue

            try:
                result = execute(dispatch, attempt_id=attempt_id)
            except BaseException as exc:
                outcome_unknown += 1
                items.append(
                    PortalHostPumpItemResult(
                        dispatch_id=dispatch_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state="OUTCOME_UNKNOWN",
                        evidence_id=attempt_evidence,
                        reason=type(exc).__name__,
                    )
                )
                continue

            if not isinstance(result, PortalHostDriverResult):
                outcome_unknown += 1
                items.append(
                    PortalHostPumpItemResult(
                        dispatch_id=dispatch_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state="OUTCOME_UNKNOWN",
                        evidence_id=attempt_evidence,
                        reason="driver returned unsupported result type",
                    )
                )
                continue

            self.store.record_reconciliation(
                dispatch_id=dispatch_id,
                state=result.state,
                evidence_id=result.evidence_id,
            )

            if result.state == "VERIFIED_COMPLETE":
                verified_complete += 1
            elif result.state == "VERIFIED_HELD":
                verified_held += 1
            elif result.state == "IN_PROGRESS":
                in_progress += 1
            elif result.state == "OUTCOME_UNKNOWN":
                outcome_unknown += 1

            items.append(
                PortalHostPumpItemResult(
                    dispatch_id=dispatch_id,
                    adapter_id=adapter_id,
                    route_id=route_id,
                    state=result.state,
                    evidence_id=result.evidence_id,
                )
            )

        return PortalHostPumpResult(
            items=tuple(items),
            attempted=attempted,
            verified_complete=verified_complete,
            verified_held=verified_held,
            in_progress=in_progress,
            outcome_unknown=outcome_unknown,
            no_driver=no_driver,
        )
