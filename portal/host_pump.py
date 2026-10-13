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
    race_lost: int
    route_unqualified: int


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

    def run_shared_pool_once(
        self,
        *,
        session_id: str | None = None,
        max_dispatches: int = 1,
    ) -> PortalHostPumpResult:
        """Pull compatible QUEUED work through the durable atomic claim path.

        This is cooperative pull *before* an effect attempt, not reallocation of
        an ATTEMPTED dispatch or reassignment of its exact node/route binding.
        It consumes only adapter IDs for which this host has a driver. All
        route/currentness and effect authority fences remain in the store.
        """
        if type(max_dispatches) is not int or max_dispatches < 1:
            raise ValueError("max_dispatches must be a positive integer")
        results: list[PortalHostPumpItemResult] = []
        complete = held = in_progress = unknown = attempted = 0
        if not self.drivers:
            return PortalHostPumpResult((), 0, 0, 0, 0, 0, 0, 0, 0)
        for _ in range(max_dispatches):
            # Generate the opaque attempt identity *before* atomic selection.
            # Unlike per-subject test factories this ID must not depend on a
            # stale snapshot of a job another host may already have taken.
            attempt_id = "portal-" + uuid.uuid4().hex
            evidence_id = self._attempt_evidence_id(attempt_id)
            dispatch = self.store.take_pending_dispatch(
                adapter_ids=self.drivers.keys(),
                attempt_id=attempt_id,
                evidence_id=evidence_id,
                session_id=session_id,
            )
            if dispatch is None:
                break
            attempted += 1
            dispatch_id = str(dispatch["dispatch_id"])
            adapter_id = str(dispatch["adapter_id"])
            route_id = str(dispatch["route_id"])
            driver = self.drivers[adapter_id]
            execute = getattr(driver, "execute", None)
            if execute is None or not callable(execute):
                unknown += 1
                results.append(PortalHostPumpItemResult(
                    dispatch_id, adapter_id, route_id, "OUTCOME_UNKNOWN",
                    evidence_id, "bound driver has no callable execute method",
                ))
                continue
            try:
                outcome = execute(dispatch, attempt_id=attempt_id)
            except Exception as exc:
                unknown += 1
                results.append(PortalHostPumpItemResult(
                    dispatch_id, adapter_id, route_id, "OUTCOME_UNKNOWN",
                    evidence_id, type(exc).__name__,
                ))
                continue
            if not isinstance(outcome, PortalHostDriverResult):
                unknown += 1
                results.append(PortalHostPumpItemResult(
                    dispatch_id, adapter_id, route_id, "OUTCOME_UNKNOWN",
                    evidence_id, "driver returned unsupported result type",
                ))
                continue
            # A recorded attempt is not success. Only the driver's reconciliation
            # evidence can resolve it; failed recording leaves the attempt
            # ambiguous and must never requeue it.
            self.store.record_reconciliation(
                dispatch_id=dispatch_id,
                state=outcome.state,
                evidence_id=outcome.evidence_id,
            )
            if outcome.state == "VERIFIED_COMPLETE":
                complete += 1
            elif outcome.state == "VERIFIED_HELD":
                held += 1
            elif outcome.state == "IN_PROGRESS":
                in_progress += 1
            else:
                unknown += 1
            results.append(PortalHostPumpItemResult(
                dispatch_id, adapter_id, route_id,
                outcome.state, outcome.evidence_id,
            ))
        return PortalHostPumpResult(
            tuple(results), attempted, complete, held,
            in_progress, unknown, 0, 0, 0,
        )

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
        race_lost = 0
        route_unqualified = 0

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

            try:
                self.store.mark_attempted(
                    dispatch_id=dispatch_id,
                    attempt_id=attempt_id,
                    evidence_id=attempt_evidence,
                )
            except ValueError:
                current = self.store.load_dispatch(dispatch_id)
                current_state = str(current.get("state") or "")
                if current_state == "ATTEMPTED":
                    race_lost += 1
                    items.append(
                        PortalHostPumpItemResult(
                            dispatch_id=dispatch_id,
                            adapter_id=adapter_id,
                            route_id=route_id,
                            state="RACE_LOST",
                            evidence_id=(
                                str(current["dispatch_evidence_id"])
                                if current.get("dispatch_evidence_id") is not None
                                else None
                            ),
                            reason="dispatch was attempted by another host",
                        )
                    )
                    continue
                if current_state == "QUEUED":
                    route_unqualified += 1
                    items.append(
                        PortalHostPumpItemResult(
                            dispatch_id=dispatch_id,
                            adapter_id=adapter_id,
                            route_id=route_id,
                            state="ROUTE_UNQUALIFIED",
                            reason=(
                                "bound route was not currently qualified "
                                "at the attempt boundary"
                            ),
                        )
                    )
                    continue
                raise
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
            except Exception as exc:
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
            race_lost=race_lost,
            route_unqualified=route_unqualified,
        )
