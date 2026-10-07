from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable, Mapping, TYPE_CHECKING

from .adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from .route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)

if TYPE_CHECKING:
    from .session import PortalSessionResult
    from .wave_runtime import PortalWavePacket


class CapabilityExecutionAdapter:
    """Compose capability-qualified routes without becoming a scheduler.

    Admission and node placement already happened before this adapter runs.
    This layer may select among host-advertised routes for the exact admitted
    packet, delegate to the owning driver, and validate returned evidence. It
    does not discover work, change placement, widen authority, or silently
    substitute a bound route.
    """

    def __init__(
        self,
        *,
        advertisement_provider: Callable[
            ["PortalSessionResult"],
            Iterable[PortalRouteAdvertisement],
        ],
        request_builder: Callable[["PortalWavePacket"], PortalRouteRequest],
        drivers: Mapping[str, object],
    ) -> None:
        self.advertisement_provider = advertisement_provider
        self.request_builder = request_builder
        self.drivers = dict(drivers)

    @staticmethod
    def _validate_request(
        packet: "PortalWavePacket",
        request: PortalRouteRequest,
    ) -> None:
        if (
            request.subject_kind != "repository"
            or request.subject_id != packet.subject_id
            or request.node_id != packet.node_id
            or request.target_kind != "repository"
            or request.target_id != packet.repository
        ):
            raise ValueError("route request does not match admitted packet")

    def select_routes(
        self,
        result: "PortalSessionResult",
    ) -> tuple[PortalRouteBinding, ...]:
        advertisements = tuple(self.advertisement_provider(result))
        bindings: list[PortalRouteBinding] = []
        for packet in result.packets:
            request = self.request_builder(packet)
            self._validate_request(packet, request)
            bindings.append(resolve_portal_route(request, advertisements))
        return tuple(bindings)

    @staticmethod
    def _route_key(
        route: PortalRouteBinding,
    ) -> tuple[str, str]:
        return (route.subject_kind, route.subject_id)

    @staticmethod
    def _record_key(
        record: PortalDispatchRecord | PortalReconciliationRecord,
    ) -> tuple[str, str]:
        return (record.subject_kind, record.subject_id)

    def _validate_dispatch_routes(
        self,
        result: "PortalSessionResult",
        routes: tuple[PortalRouteBinding, ...],
    ) -> dict[tuple[str, str], PortalRouteBinding]:
        expected = {
            ("repository", packet.subject_id)
            for packet in result.packets
        }
        by_key: dict[tuple[str, str], PortalRouteBinding] = {}
        for route in routes:
            key = self._route_key(route)
            if key in by_key:
                raise ValueError("duplicate execution route binding")
            by_key[key] = route
        if set(by_key) != expected:
            raise ValueError(
                "execution routes do not match admitted packet set"
            )
        return by_key

    def _require_drivers(
        self,
        adapter_ids: Iterable[str],
    ) -> None:
        missing = sorted(
            {
                adapter_id
                for adapter_id in adapter_ids
                if adapter_id not in self.drivers
            }
        )
        if missing:
            raise ValueError(
                "missing execution driver: " + ", ".join(missing)
            )

    def dispatch(
        self,
        result: "PortalSessionResult",
        routes: tuple[PortalRouteBinding, ...],
    ) -> tuple[PortalDispatchRecord, ...]:
        by_key = self._validate_dispatch_routes(result, routes)
        self._require_drivers(route.adapter_id for route in routes)

        grouped: dict[str, list[PortalRouteBinding]] = defaultdict(list)
        for route in routes:
            grouped[route.adapter_id].append(route)

        records: list[PortalDispatchRecord] = []
        seen_records: set[tuple[str, str]] = set()
        for adapter_id in sorted(grouped):
            driver = self.drivers[adapter_id]
            driver_routes = tuple(grouped[adapter_id])
            returned = tuple(driver.dispatch(result, driver_routes))
            allowed = {
                self._route_key(route): route
                for route in driver_routes
            }
            for record in returned:
                key = self._record_key(record)
                route = allowed.get(key)
                if route is None:
                    raise ValueError(
                        "execution driver returned evidence for unbound subject"
                    )
                if key in seen_records:
                    raise ValueError(
                        "execution driver returned duplicate subject evidence"
                    )
                if (
                    record.adapter_id != route.adapter_id
                    or record.route_id != route.route_id
                ):
                    raise ValueError(
                        "execution driver evidence differs from bound route"
                    )
                seen_records.add(key)
                records.append(record)

            if {
                self._record_key(record)
                for record in returned
            } != set(allowed):
                raise ValueError(
                    "execution driver must return one dispatch record "
                    "per delegated route"
                )

        if seen_records != set(by_key):
            raise ValueError(
                "execution drivers did not cover all bound routes"
            )
        return tuple(records)

    def reconcile(
        self,
        status: Mapping[str, object],
    ) -> tuple[PortalReconciliationRecord, ...]:
        raw_subjects = status.get("subjects")
        if not isinstance(raw_subjects, list):
            raise ValueError("Portal status subjects must be a list")

        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        route_by_key: dict[
            tuple[str, str],
            tuple[str, str],
        ] = {}

        for raw in raw_subjects:
            if not isinstance(raw, Mapping):
                raise ValueError("Portal status subject must be a mapping")
            if raw.get("state") != "ACTIVE":
                continue

            adapter_id = raw.get("adapter_id")
            route_id = raw.get("route_id")
            if adapter_id is None and route_id is None:
                continue
            if not (
                isinstance(adapter_id, str)
                and adapter_id
                and isinstance(route_id, str)
                and route_id
            ):
                raise ValueError(
                    "active routed subject has incomplete route binding"
                )

            subject_kind = raw.get("subject_kind")
            subject_id = raw.get("subject_id")
            if not (
                isinstance(subject_kind, str)
                and subject_kind
                and isinstance(subject_id, str)
                and subject_id
            ):
                raise ValueError(
                    "active routed subject has incomplete identity"
                )

            key = (subject_kind, subject_id)
            if key in route_by_key:
                raise ValueError("duplicate active routed subject")
            route_by_key[key] = (adapter_id, route_id)
            grouped[adapter_id].append(raw)

        self._require_drivers(grouped)

        records: list[PortalReconciliationRecord] = []
        seen: set[tuple[str, str]] = set()
        for adapter_id in sorted(grouped):
            driver = self.drivers[adapter_id]
            subjects = tuple(grouped[adapter_id])
            returned = tuple(driver.reconcile(status, subjects))
            for record in returned:
                key = self._record_key(record)
                expected_route = route_by_key.get(key)
                if expected_route is None:
                    raise ValueError(
                        "execution driver reconciled unbound subject"
                    )
                if key in seen:
                    raise ValueError(
                        "execution driver returned duplicate reconciliation"
                    )
                if (
                    record.adapter_id != expected_route[0]
                    or record.route_id != expected_route[1]
                ):
                    raise ValueError(
                        "reconciliation evidence differs from bound route"
                    )
                seen.add(key)
                records.append(record)

        return tuple(records)
