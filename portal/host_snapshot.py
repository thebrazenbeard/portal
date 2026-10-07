from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping

from .route_resolver import PortalRouteAdvertisement


_SCHEMA = "PORTAL_HOST_CAPABILITY_SNAPSHOT_V1"
_TOP_LEVEL_KEYS = frozenset(
    {"schema", "observed_at", "ttl_seconds", "routes", "occupancy"}
)
_ROUTE_KEYS = frozenset(
    {
        "adapter_id",
        "route_id",
        "node_id",
        "target_kind",
        "target_id",
        "capabilities",
        "effect_capabilities",
        "authorized_effects",
        "available",
        "attached",
        "current",
        "preference",
    }
)
_OCCUPANCY_KEYS = frozenset({"node_id", "occupied_slots"})


@dataclass(frozen=True)
class PortalHostCapabilitySnapshot:
    observed_at: float
    ttl_seconds: float
    routes: tuple[PortalRouteAdvertisement, ...]
    occupancy: tuple[tuple[str, int], ...]

    @property
    def expires_at(self) -> float:
        return self.observed_at + self.ttl_seconds


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    label: str,
) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{label} missing required field: {missing[0]}")
    unexpected = sorted(set(value) - required)
    if unexpected:
        raise ValueError(f"{label} has unexpected field: {unexpected[0]}")


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} entries must be non-empty strings")
        result.append(item.strip())
    return tuple(result)


def load_host_capability_snapshot(path: Path) -> PortalHostCapabilitySnapshot:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    payload = _mapping(raw, "host capability snapshot")
    _exact_keys(
        payload,
        required=_TOP_LEVEL_KEYS,
        label="host capability snapshot",
    )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unexpected host capability snapshot schema")

    observed_at = _finite_number(payload["observed_at"], "observed_at")
    ttl_seconds = _finite_number(payload["ttl_seconds"], "ttl_seconds")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    raw_routes = payload["routes"]
    if not isinstance(raw_routes, list):
        raise ValueError("routes must be an array")
    routes: list[PortalRouteAdvertisement] = []
    for index, raw_route in enumerate(raw_routes):
        route = _mapping(raw_route, f"routes[{index}]")
        _exact_keys(route, required=_ROUTE_KEYS, label=f"routes[{index}]")
        routes.append(
            PortalRouteAdvertisement(
                adapter_id=route["adapter_id"],
                route_id=route["route_id"],
                node_id=route["node_id"],
                target_kind=route["target_kind"],
                target_id=route["target_id"],
                capabilities=_string_list(
                    route["capabilities"],
                    f"routes[{index}].capabilities",
                ),
                effect_capabilities=_string_list(
                    route["effect_capabilities"],
                    f"routes[{index}].effect_capabilities",
                ),
                authorized_effects=_string_list(
                    route["authorized_effects"],
                    f"routes[{index}].authorized_effects",
                ),
                available=route["available"],
                attached=route["attached"],
                current=route["current"],
                preference=route["preference"],
            )
        )

    raw_occupancy = payload["occupancy"]
    if not isinstance(raw_occupancy, list):
        raise ValueError("occupancy must be an array")
    occupancy: list[tuple[str, int]] = []
    seen_nodes: set[str] = set()
    for index, raw_item in enumerate(raw_occupancy):
        item = _mapping(raw_item, f"occupancy[{index}]")
        _exact_keys(item, required=_OCCUPANCY_KEYS, label=f"occupancy[{index}]")
        node_id = item["node_id"]
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"occupancy[{index}].node_id is required")
        node_id = node_id.strip()
        occupied_slots = item["occupied_slots"]
        if type(occupied_slots) is not int or occupied_slots < 0:
            raise ValueError(
                f"occupancy[{index}].occupied_slots must be a non-negative integer"
            )
        if node_id in seen_nodes:
            raise ValueError("duplicate node in occupancy snapshot")
        seen_nodes.add(node_id)
        occupancy.append((node_id, occupied_slots))

    if not routes and not occupancy:
        raise ValueError("host capability snapshot must contain an observation")

    return PortalHostCapabilitySnapshot(
        observed_at=observed_at,
        ttl_seconds=ttl_seconds,
        routes=tuple(routes),
        occupancy=tuple(occupancy),
    )
