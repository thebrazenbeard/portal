from __future__ import annotations

from pathlib import Path

import pytest

from portal.host_bridge import (
    PortalHostBridgeStore,
    PortalHostNodeCurrentness,
)


def test_host_node_occupancy_expires_and_fails_closed_when_required(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=8,
        ttl_seconds=60.0,
        observed_at=100.0,
    )
    store.advertise_node_occupancy(
        node_id="worklaptop",
        occupied_slots=0,
        ttl_seconds=60.0,
        observed_at=100.0,
    )

    provider = PortalHostNodeCurrentness(
        store=store,
        required_node_ids=("lappy", "worklaptop"),
        clock=lambda: 120.0,
    )
    assert provider() == {"lappy": 8, "worklaptop": 0}

    expired = PortalHostNodeCurrentness(
        store=store,
        required_node_ids=("lappy", "worklaptop"),
        clock=lambda: 161.0,
    )
    with pytest.raises(ValueError, match="missing current host occupancy"):
        expired()
    store.close()


def test_host_node_occupancy_rejects_negative_counts(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    with pytest.raises(ValueError, match="non-negative"):
        store.advertise_node_occupancy(
            node_id="lappy",
            occupied_slots=-1,
            ttl_seconds=60.0,
            observed_at=100.0,
        )
    store.close()


def test_older_host_occupancy_observation_cannot_overwrite_newer_state(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=3,
        ttl_seconds=60.0,
        observed_at=200.0,
    )

    with pytest.raises(ValueError, match="stale host occupancy observation"):
        store.advertise_node_occupancy(
            node_id="lappy",
            occupied_slots=9,
            ttl_seconds=60.0,
            observed_at=100.0,
        )

    assert store.active_node_occupancy(now=220.0) == {"lappy": 3}
    store.close()


def test_same_timestamp_conflicting_occupancy_is_refused(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    first = store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=3,
        ttl_seconds=60.0,
        observed_at=200.0,
    )
    replay = store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=3,
        ttl_seconds=60.0,
        observed_at=200.0,
    )
    assert replay == first

    with pytest.raises(ValueError, match="conflicting host occupancy observation"):
        store.advertise_node_occupancy(
            node_id="lappy",
            occupied_slots=4,
            ttl_seconds=60.0,
            observed_at=200.0,
        )
    store.close()


def test_host_currentness_can_require_only_unsourced_nodes(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_node_occupancy(
        node_id="worklaptop",
        occupied_slots=1,
        ttl_seconds=60.0,
        observed_at=100.0,
    )
    provider = PortalHostNodeCurrentness(
        store=store,
        required_node_ids=("worklaptop",),
        clock=lambda: 120.0,
    )
    assert provider() == {"worklaptop": 1}
    store.close()
