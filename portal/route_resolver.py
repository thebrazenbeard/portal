from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .adapters import PortalRouteBinding


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _normalized(values: Iterable[str], label: str) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        normalized.add(_required(value, label))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class PortalRouteAdvertisement:
    adapter_id: str
    route_id: str
    node_id: str
    target_kind: str
    target_id: str
    capabilities: tuple[str, ...]
    effect_capabilities: tuple[str, ...]
    authorized_effects: tuple[str, ...]
    available: bool
    attached: bool
    current: bool
    preference: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _required(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "route_id", _required(self.route_id, "route_id"))
        object.__setattr__(self, "node_id", _required(self.node_id, "node_id"))
        object.__setattr__(
            self,
            "target_kind",
            _required(self.target_kind, "target_kind"),
        )
        object.__setattr__(self, "target_id", _required(self.target_id, "target_id"))
        object.__setattr__(
            self,
            "capabilities",
            _normalized(self.capabilities, "capability"),
        )
        object.__setattr__(
            self,
            "effect_capabilities",
            _normalized(self.effect_capabilities, "effect capability"),
        )
        object.__setattr__(
            self,
            "authorized_effects",
            _normalized(self.authorized_effects, "authorized effect"),
        )
        for field_name in ("available", "attached", "current"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        if type(self.preference) is not int:
            raise ValueError("preference must be an integer")


@dataclass(frozen=True)
class PortalRouteRequest:
    subject_kind: str
    subject_id: str
    node_id: str
    target_kind: str
    target_id: str
    required_capabilities: tuple[str, ...]
    required_effect: str
    preferred_adapter_id: str | None = None
    preferred_route_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_kind",
            _required(self.subject_kind, "subject_kind"),
        )
        object.__setattr__(self, "subject_id", _required(self.subject_id, "subject_id"))
        object.__setattr__(self, "node_id", _required(self.node_id, "node_id"))
        object.__setattr__(
            self,
            "target_kind",
            _required(self.target_kind, "target_kind"),
        )
        object.__setattr__(self, "target_id", _required(self.target_id, "target_id"))
        object.__setattr__(
            self,
            "required_capabilities",
            _normalized(self.required_capabilities, "required capability"),
        )
        object.__setattr__(
            self,
            "required_effect",
            _required(self.required_effect, "required_effect"),
        )

        preferred_adapter = self.preferred_adapter_id
        preferred_route = self.preferred_route_id
        if (preferred_adapter is None) != (preferred_route is None):
            raise ValueError("preferred adapter and route must be paired")
        if preferred_adapter is not None:
            object.__setattr__(
                self,
                "preferred_adapter_id",
                _required(preferred_adapter, "preferred_adapter_id"),
            )
            object.__setattr__(
                self,
                "preferred_route_id",
                _required(preferred_route, "preferred_route_id"),
            )


def _qualified(
    request: PortalRouteRequest,
    route: PortalRouteAdvertisement,
) -> bool:
    return (
        route.available
        and route.attached
        and route.current
        and route.node_id == request.node_id
        and route.target_kind == request.target_kind
        and route.target_id == request.target_id
        and set(request.required_capabilities).issubset(route.capabilities)
        and request.required_effect in route.effect_capabilities
        and request.required_effect in route.authorized_effects
    )


def resolve_portal_route(
    request: PortalRouteRequest,
    routes: Iterable[PortalRouteAdvertisement],
) -> PortalRouteBinding:
    advertisements = tuple(routes)
    identities = [
        (
            route.adapter_id,
            route.route_id,
            route.node_id,
            route.target_kind,
            route.target_id,
        )
        for route in advertisements
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate execution route advertisement")

    preferred = request.preferred_adapter_id
    if preferred is not None:
        exact = tuple(
            route
            for route in advertisements
            if route.adapter_id == preferred
            and route.route_id == request.preferred_route_id
            and _qualified(request, route)
        )
        if len(exact) != 1:
            raise ValueError("explicit route is not currently qualified")
        selected = exact[0]
    else:
        qualified = tuple(
            route for route in advertisements if _qualified(request, route)
        )
        if not qualified:
            raise ValueError("no qualified execution route")
        selected = sorted(
            qualified,
            key=lambda route: (
                -route.preference,
                route.adapter_id,
                route.route_id,
            ),
        )[0]

    return PortalRouteBinding(
        subject_kind=request.subject_kind,
        subject_id=request.subject_id,
        adapter_id=selected.adapter_id,
        route_id=selected.route_id,
    )
