from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping

import yaml

from .route_resolver import PortalRouteAdvertisement


_SCHEMA = "PORTAL_HOST_AUTHORITY_V1"
_ALLOWED_TOP_LEVEL = {"schema", "grants"}
_ALLOWED_GRANT_KEYS = {
    "adapter_id",
    "route_id",
    "node_id",
    "target_kind",
    "target_id",
    "authorized_effects",
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _effects(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("authorized_effects must be a list")
    result = tuple(
        sorted(
            {
                _required_text(item, "authorized effect")
                for item in value
            }
        )
    )
    return result


RouteIdentity = tuple[str, str, str, str, str]
HostAuthorityGrants = dict[RouteIdentity, tuple[str, ...]]


def _identity(
    *,
    adapter_id: str,
    route_id: str,
    node_id: str,
    target_kind: str,
    target_id: str,
) -> RouteIdentity:
    return (
        _required_text(adapter_id, "adapter_id"),
        _required_text(route_id, "route_id"),
        _required_text(node_id, "node_id"),
        _required_text(target_kind, "target_kind"),
        _required_text(target_id, "target_id"),
    )


def load_host_authority(path: Path) -> HostAuthorityGrants:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("host authority manifest must be a mapping")
    if set(payload) != _ALLOWED_TOP_LEVEL:
        raise ValueError(
            "host authority manifest requires exactly schema and grants"
        )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported host authority manifest schema")

    raw_grants = payload["grants"]
    if not isinstance(raw_grants, list):
        raise ValueError("host authority grants must be a list")

    result: HostAuthorityGrants = {}
    for raw in raw_grants:
        if not isinstance(raw, Mapping):
            raise ValueError("host authority grant must be a mapping")
        if set(raw) != _ALLOWED_GRANT_KEYS:
            missing = sorted(_ALLOWED_GRANT_KEYS - set(raw))
            if missing:
                raise ValueError(
                    "host authority grant missing required field: "
                    + missing[0]
                )
            unexpected = sorted(set(raw) - _ALLOWED_GRANT_KEYS)
            raise ValueError(
                "host authority grant contains unsupported field: "
                + unexpected[0]
            )

        key = _identity(
            adapter_id=raw["adapter_id"],
            route_id=raw["route_id"],
            node_id=raw["node_id"],
            target_kind=raw["target_kind"],
            target_id=raw["target_id"],
        )
        if key in result:
            raise ValueError("duplicate host authority grant")
        result[key] = _effects(raw["authorized_effects"])

    return result


def apply_host_authority(
    routes: tuple[PortalRouteAdvertisement, ...],
    grants: HostAuthorityGrants,
) -> tuple[PortalRouteAdvertisement, ...]:
    result: list[PortalRouteAdvertisement] = []
    for route in routes:
        key = (
            route.adapter_id,
            route.route_id,
            route.node_id,
            route.target_kind,
            route.target_id,
        )
        authorized = grants.get(key, ())
        if not set(authorized).issubset(route.effect_capabilities):
            raise ValueError(
                "host authority grant exceeds observed effect capability"
            )
        result.append(
            replace(route, authorized_effects=tuple(authorized))
        )
    return tuple(result)
