from __future__ import annotations

from typing import Any

from .host_bridge import PortalHostBridgeStore
from .route_resolver import PortalRouteAdvertisement


def _route_payload(route: PortalRouteAdvertisement) -> dict[str, object]:
    return {
        "adapter_id": route.adapter_id,
        "route_id": route.route_id,
        "node_id": route.node_id,
        "target_kind": route.target_kind,
        "target_id": route.target_id,
        "capabilities": list(route.capabilities),
        "effect_capabilities": list(route.effect_capabilities),
        "authorized_effects": list(route.authorized_effects),
        "available": route.available,
        "attached": route.attached,
        "current": route.current,
        "preference": route.preference,
    }


def build_host_diagnostics(
    store: PortalHostBridgeStore,
    *,
    session_id: str,
) -> dict[str, object]:
    """Build a client-independent read model for host execution state."""
    pending = tuple(
        store.pending_dispatch_diagnostics(session_id=session_id)
    )
    unresolved = tuple(
        store.unresolved_dispatches(session_id=session_id)
    )
    routes = tuple(store.active_routes())
    occupancy = store.active_node_occupancy()

    qualified = sum(
        bool(item.get("route_qualified"))
        for item in pending
    )
    unqualified = len(pending) - qualified
    reconciliation_required = len(unresolved)

    if reconciliation_required or unqualified:
        attention_state = "ACTION_REQUIRED"
    elif qualified:
        attention_state = "READY"
    else:
        attention_state = "CLEAR"

    return {
        "pending": {
            "count": len(pending),
            "qualified": qualified,
            "unqualified": unqualified,
            "items": list(pending),
        },
        "unresolved": {
            "count": reconciliation_required,
            "items": list(unresolved),
        },
        "routes": {
            "count": len(routes),
            "items": [_route_payload(route) for route in routes],
        },
        "node_occupancy": dict(occupancy),
        "attention": {
            "state": attention_state,
            "ready_to_attempt": qualified,
            "route_refresh_required": unqualified,
            "reconciliation_required": reconciliation_required,
        },
    }
