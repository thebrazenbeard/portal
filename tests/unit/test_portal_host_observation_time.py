from __future__ import annotations

from pathlib import Path

from portal.frontier_currentness import (
    PortalFrontierObservation,
    PortalHostFrontierStore,
)
from portal.host_bridge import PortalHostBridgeStore
from portal.route_resolver import PortalRouteAdvertisement


def test_future_dated_route_is_not_active_before_observation_time(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id="github",
            route_id="repo-native",
            node_id="repo-native",
            target_kind="repository",
            target_id="thebrazenbeard/portal",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True,
            attached=True,
            current=True,
            preference=50,
        ),
        observed_at=200.0,
        ttl_seconds=100.0,
    )

    assert store.active_routes(now=199.0) == ()
    assert len(store.active_routes(now=200.0)) == 1
    store.close()


def test_future_dated_occupancy_is_not_active_before_observation_time(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=8,
        observed_at=200.0,
        ttl_seconds=100.0,
    )

    assert store.active_node_occupancy(now=199.0) == {}
    assert store.active_node_occupancy(now=200.0) == {"lappy": 8}
    store.close()


def test_future_dated_frontier_is_not_active_before_observation_time(
    tmp_path: Path,
) -> None:
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    observation = PortalFrontierObservation(
        subject_kind="repository",
        subject_id="portal",
        repository="thebrazenbeard/portal",
        ref="work/portal-coordinator-v1",
        exact_head="a" * 40,
        frontier_sha256="b" * 64,
        disposition="CURRENT",
    )
    store.advertise(
        observation,
        observed_at=200.0,
        ttl_seconds=100.0,
    )

    assert store.active(now=199.0) == {}
    assert store.active(now=200.0) == {
        ("repository", "portal"): observation,
    }
    store.close()
