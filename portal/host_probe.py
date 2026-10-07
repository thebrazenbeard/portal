from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

from .route_resolver import PortalRouteAdvertisement


_RESULT_SCHEMA = "PORTAL_HOST_PROBE_RESULT_V1"
_RESULT_KEYS = frozenset(
    {"schema", "adapter_id", "evidence_id", "routes", "occupancy"}
)
_ROUTE_KEYS = frozenset(
    {
        "route_id",
        "node_id",
        "target_kind",
        "target_id",
        "capabilities",
        "effect_capabilities",
        "available",
        "attached",
        "current",
        "preference",
    }
)
_OCCUPANCY_KEYS = frozenset({"node_id", "occupied_slots"})


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _exact_keys(
    payload: Mapping[str, object],
    *,
    expected: frozenset[str],
    label: str,
) -> None:
    unexpected = sorted(set(payload) - expected)
    if unexpected:
        raise ValueError(f"{label} has unexpected field: {unexpected[0]}")
    missing = sorted(expected - set(payload))
    if missing:
        raise ValueError(f"{label} missing required field: {missing[0]}")


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        result.append(_required_text(item, f"{label} entry"))
    return tuple(result)


@dataclass(frozen=True)
class PortalHostProbeResult:
    adapter_id: str
    evidence_id: str
    routes: tuple[PortalRouteAdvertisement, ...]
    occupancy: tuple[tuple[str, int], ...]


class PortalCommandHostProbe:
    """Run one observation-only host capability probe.

    Probe results may describe availability, attachment, currentness and
    technical capability. They cannot carry or create effect authority.
    """

    def __init__(
        self,
        *,
        adapter_id: str,
        command: Sequence[str],
        timeout_seconds: float = 30.0,
        cwd: Path | None = None,
    ) -> None:
        self.adapter_id = _required_text(adapter_id, "adapter_id")
        if isinstance(command, (str, bytes)):
            raise ValueError("probe command must be a non-empty argv sequence")
        argv = tuple(command)
        if not argv or any(
            not isinstance(part, str) or not part.strip()
            for part in argv
        ):
            raise ValueError(
                "probe command must contain non-empty argv strings"
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or float(timeout_seconds) <= 0
        ):
            raise ValueError("probe timeout must be positive")
        self.command = tuple(part.strip() for part in argv)
        self.timeout_seconds = float(timeout_seconds)
        self.cwd = Path(cwd) if cwd is not None else None

    def observe(self) -> PortalHostProbeResult:
        completed = subprocess.run(
            self.command,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            cwd=self.cwd,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "host probe exited with exit code "
                f"{completed.returncode}"
            )

        try:
            raw = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("host probe result must be valid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("host probe result must be a JSON object")
        _exact_keys(raw, expected=_RESULT_KEYS, label="host probe result")
        if raw["schema"] != _RESULT_SCHEMA:
            raise ValueError(
                f"host probe result schema must be {_RESULT_SCHEMA}"
            )
        observed_adapter = _required_text(
            raw["adapter_id"],
            "host probe result adapter_id",
        )
        if observed_adapter != self.adapter_id:
            raise ValueError(
                "host probe result adapter_id does not match configured adapter"
            )
        evidence_id = _required_text(
            raw["evidence_id"],
            "host probe result evidence_id",
        )

        raw_routes = raw["routes"]
        if not isinstance(raw_routes, list):
            raise ValueError("host probe routes must be an array")
        routes: list[PortalRouteAdvertisement] = []
        for index, raw_route in enumerate(raw_routes):
            if not isinstance(raw_route, Mapping):
                raise ValueError(f"host probe routes[{index}] must be an object")
            _exact_keys(
                raw_route,
                expected=_ROUTE_KEYS,
                label=f"host probe routes[{index}]",
            )
            routes.append(
                PortalRouteAdvertisement(
                    adapter_id=self.adapter_id,
                    route_id=raw_route["route_id"],
                    node_id=raw_route["node_id"],
                    target_kind=raw_route["target_kind"],
                    target_id=raw_route["target_id"],
                    capabilities=_string_list(
                        raw_route["capabilities"],
                        f"host probe routes[{index}].capabilities",
                    ),
                    effect_capabilities=_string_list(
                        raw_route["effect_capabilities"],
                        f"host probe routes[{index}].effect_capabilities",
                    ),
                    authorized_effects=(),
                    available=raw_route["available"],
                    attached=raw_route["attached"],
                    current=raw_route["current"],
                    preference=raw_route["preference"],
                )
            )
        route_keys = tuple(
            (
                route.adapter_id,
                route.route_id,
                route.node_id,
                route.target_kind,
                route.target_id,
            )
            for route in routes
        )
        if len(set(route_keys)) != len(route_keys):
            raise ValueError("duplicate route in host probe result")

        raw_occupancy = raw["occupancy"]
        if not isinstance(raw_occupancy, list):
            raise ValueError("host probe occupancy must be an array")
        occupancy: list[tuple[str, int]] = []
        seen_nodes: set[str] = set()
        for index, raw_item in enumerate(raw_occupancy):
            if not isinstance(raw_item, Mapping):
                raise ValueError(
                    f"host probe occupancy[{index}] must be an object"
                )
            _exact_keys(
                raw_item,
                expected=_OCCUPANCY_KEYS,
                label=f"host probe occupancy[{index}]",
            )
            node_id = _required_text(
                raw_item["node_id"],
                f"host probe occupancy[{index}].node_id",
            )
            occupied_slots = raw_item["occupied_slots"]
            if type(occupied_slots) is not int or occupied_slots < 0:
                raise ValueError(
                    "host probe occupied_slots must be a non-negative integer"
                )
            if node_id in seen_nodes:
                raise ValueError(
                    "duplicate node in host probe occupancy"
                )
            seen_nodes.add(node_id)
            occupancy.append((node_id, occupied_slots))

        return PortalHostProbeResult(
            adapter_id=self.adapter_id,
            evidence_id=evidence_id,
            routes=tuple(routes),
            occupancy=tuple(occupancy),
        )
