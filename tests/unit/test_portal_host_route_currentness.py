from __future__ import annotations

from pathlib import Path

import pytest

from portal.host_bridge import PortalHostBridgeStore
from portal.route_resolver import PortalRouteAdvertisement


def _route(
    *,
    available: bool = True,
    attached: bool = True,
    current: bool = True,
    authorized_effects: tuple[str, ...] = ("SOURCE_ONLY",),
) -> PortalRouteAdvertisement:
    return PortalRouteAdvertisement(
        adapter_id="github",
        route_id="repo-native",
        node_id="repo-native",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        capabilities=("semantic_work",),
        effect_capabilities=("SOURCE_ONLY",),
        authorized_effects=authorized_effects,
        available=available,
        attached=attached,
        current=current,
        preference=50,
    )


def test_stale_route_observation_cannot_overwrite_newer_current_state(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(
        _route(),
        ttl_seconds=300.0,
        observed_at=200.0,
    )

    with pytest.raises(ValueError, match="stale host route observation"):
        store.advertise_route(
            _route(
                available=False,
                attached=False,
                current=False,
                authorized_effects=("NO_PROTECTED_EFFECT",),
            ),
            ttl_seconds=300.0,
            observed_at=100.0,
        )

    routes = store.active_routes(now=220.0)
    assert len(routes) == 1
    assert routes[0].available is True
    assert routes[0].attached is True
    assert routes[0].current is True
    assert routes[0].authorized_effects == ("SOURCE_ONLY",)
    store.close()


def test_same_timestamp_exact_route_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    route = _route()
    first = store.advertise_route(
        route,
        ttl_seconds=300.0,
        observed_at=200.0,
    )
    replay = store.advertise_route(
        route,
        ttl_seconds=300.0,
        observed_at=200.0,
    )

    assert replay == first
    assert store.active_routes(now=220.0) == (route,)
    store.close()


def test_same_timestamp_conflicting_route_observation_is_refused(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(
        _route(),
        ttl_seconds=300.0,
        observed_at=200.0,
    )

    with pytest.raises(ValueError, match="conflicting host route observation"):
        store.advertise_route(
            _route(current=False),
            ttl_seconds=300.0,
            observed_at=200.0,
        )
    store.close()
