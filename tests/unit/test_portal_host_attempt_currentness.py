from __future__ import annotations

from pathlib import Path
from contextlib import closing
import shutil
import sqlite3

import pytest

from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


class _FakeMonotonicHost:
    """Cloneable source-test oracle; NOT durable host authority."""

    def __init__(self) -> None:
        self.state: dict[str, tuple[int, str]] = {}

    def current(self, scope: str) -> tuple[int, str] | None:
        return self.state.get(scope)

    def compare_advance(self, scope: str, epoch: int,
                        prior_root: str, next_root: str) -> bool:
        if self.state.get(scope) != (epoch, prior_root):
            return False
        self.state[scope] = (epoch + 1, next_root)
        return True


def _anchored_existing_dispatch(tmp_path: Path):
    db_path = tmp_path / "host-bridge.sqlite"
    store = PortalHostBridgeStore(db_path)
    _advertise(store, subject_id="portal", route_id="github:portal",
               observed_at=100.0, ttl_seconds=300.0)
    dispatch_id = _queue(store, run_id="portfolio::g1", subject_id="portal",
                         route_id="github:portal", queued_at=110.0)
    root = store._state_root()  # independently provisioned by fake host fixture
    store.close()
    host = _FakeMonotonicHost()
    host.state["host:portal"] = (0, root)
    store = PortalHostBridgeStore(db_path, monotonic_anchor=host,
                                  monotonic_scope="host:portal")
    store.provision_host_checkpoint()
    return store, host, dispatch_id


def _sqlite_backup(src_path: Path, dest_path: Path) -> None:
    with closing(sqlite3.connect(src_path)) as src, closing(sqlite3.connect(dest_path)) as dst:
        src.backup(dst)


def test_host_anchor_blocks_snapshot_restore_replayed_attempt(tmp_path: Path) -> None:
    store, host, dispatch_id = _anchored_existing_dispatch(tmp_path)
    snapshot = tmp_path / "before-attempt.sqlite"
    _sqlite_backup(store.path, snapshot)
    attempted = store.mark_attempted(
        dispatch_id=dispatch_id, attempt_id="attempt-1", evidence_id="receipt-1",
        attempted_at=121.0)
    assert attempted["state"] == "ATTEMPTED"
    assert host.current("host:portal")[0] == 1
    store.close()
    shutil.copyfile(snapshot, tmp_path / "host-bridge.sqlite")
    restored = PortalHostBridgeStore(tmp_path / "host-bridge.sqlite",
                                     monotonic_anchor=host,
                                     monotonic_scope="host:portal")
    with pytest.raises(PermissionError, match="checkpoint mismatch"):
        restored.mark_attempted(
            dispatch_id=dispatch_id, attempt_id="attempt-2", evidence_id="receipt-2",
            attempted_at=121.0)
    assert restored.load_dispatch(dispatch_id)["state"] == "QUEUED"
    restored.close()


def test_host_anchor_rejects_tampered_authorized_route_without_cas(tmp_path: Path) -> None:
    store, host, dispatch_id = _anchored_existing_dispatch(tmp_path)
    with closing(sqlite3.connect(store.path)) as attacker:
        attacker.execute(
            "UPDATE portal_host_routes SET authorized_effects_json=?",
            ('["SOURCE_ONLY","SOURCE_WRITE"]',))
        attacker.commit()
    with pytest.raises(PermissionError, match="checkpoint mismatch"):
        store.take_pending_dispatch(adapter_ids=("github",),
                                    attempt_id="bad", evidence_id="receipt:bad",
                                    attempted_at=121.0)
    assert host.current("host:portal")[0] == 0
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_host_anchor_advances_on_legitimate_route_and_queue_updates(tmp_path: Path) -> None:
    store, host, dispatch_id = _anchored_existing_dispatch(tmp_path)
    _advertise(store, subject_id="portal", route_id="github:portal",
               observed_at=130.0, ttl_seconds=300.0)
    assert host.current("host:portal")[0] == 1
    taken = store.take_pending_dispatch(adapter_ids=("github",),
                                        attempt_id="attempt-1", evidence_id="receipt-1",
                                        attempted_at=131.0)
    assert taken is not None and taken["dispatch_id"] == dispatch_id
    assert taken["state"] == "ATTEMPTED"
    assert host.current("host:portal")[0] == 2
    store.close()


def test_host_anchor_missing_provisioning_denies_attempt(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite",
                                  monotonic_anchor=_FakeMonotonicHost(),
                                  monotonic_scope="host:portal")
    with pytest.raises(PermissionError, match="checkpoint mismatch"):
        store.take_pending_dispatch(adapter_ids=("github",),
                                    attempt_id="unprovisioned", evidence_id="none")
    store.close()


def test_enrolled_database_rejects_unanchored_reader_downgrade(tmp_path: Path) -> None:
    store, host, dispatch_id = _anchored_existing_dispatch(tmp_path)
    snapshot = tmp_path / "before-attempt.sqlite"
    _sqlite_backup(store.path, snapshot)
    store.mark_attempted(dispatch_id=dispatch_id, attempt_id="first",
                         evidence_id="receipt-1", attempted_at=121.0)
    store.close()
    shutil.copyfile(snapshot, tmp_path / "host-bridge.sqlite")
    with pytest.raises(PermissionError, match="requires an anchor"):
        PortalHostBridgeStore(tmp_path / "host-bridge.sqlite")
    assert host.current("host:portal")[0] == 1


def test_enrolled_database_rejects_reprovisioned_scope_alias(tmp_path: Path) -> None:
    store, host, dispatch_id = _anchored_existing_dispatch(tmp_path)
    snapshot = tmp_path / "before-attempt.sqlite"
    _sqlite_backup(store.path, snapshot)
    initial_state = host.current("host:portal")
    store.mark_attempted(dispatch_id=dispatch_id, attempt_id="first",
                         evidence_id="receipt-1", attempted_at=121.0)
    store.close()
    shutil.copyfile(snapshot, tmp_path / "host-bridge.sqlite")
    host.state["host:alias"] = initial_state
    aliased = PortalHostBridgeStore(tmp_path / "host-bridge.sqlite",
                                    monotonic_anchor=host,
                                    monotonic_scope="host:alias")
    with pytest.raises(PermissionError, match="checkpoint mismatch"):
        aliased.mark_attempted(dispatch_id=dispatch_id, attempt_id="second",
                               evidence_id="receipt-2", attempted_at=121.0)
    aliased.close()


def _queue(
    store: PortalHostBridgeStore,
    *,
    run_id: str,
    subject_id: str,
    route_id: str,
    queued_at: float,
) -> str:
    packet = PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=f"thebrazenbeard/{subject_id}",
        ref="main",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage:{subject_id}",
        work_fingerprint="c" * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Advance {subject_id}.",
        lead_identity="vera",
        reviewer_identities=(),
    )
    result = PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id=run_id,
        packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    binding = PortalRouteBinding(
        subject_kind="repository",
        subject_id=subject_id,
        adapter_id="github",
        route_id=route_id,
    )
    queued = store.queue_dispatch(
        result=result,
        packet=packet,
        binding=binding,
        queued_at=queued_at,
    )
    return str(queued["dispatch_id"])


def _advertise(
    store: PortalHostBridgeStore,
    *,
    subject_id: str,
    route_id: str,
    observed_at: float,
    ttl_seconds: float,
) -> None:
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id="github",
            route_id=route_id,
            node_id="repo-native",
            target_kind="repository",
            target_id=f"thebrazenbeard/{subject_id}",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True,
            attached=True,
            current=True,
            preference=50,
        ),
        observed_at=observed_at,
        ttl_seconds=ttl_seconds,
    )


def test_attempt_refuses_bound_route_that_expired_after_queue(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    _advertise(
        store,
        subject_id="portal",
        route_id="github:portal",
        observed_at=100.0,
        ttl_seconds=60.0,
    )
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
        route_id="github:portal",
        queued_at=120.0,
    )

    with pytest.raises(ValueError, match="route is not currently qualified"):
        store.mark_attempted(
            dispatch_id=dispatch_id,
            attempt_id="late-attempt",
            evidence_id="request:late",
            attempted_at=161.0,
        )

    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_exact_attempt_replay_remains_idempotent_after_route_expires(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    _advertise(
        store,
        subject_id="portal",
        route_id="github:portal",
        observed_at=100.0,
        ttl_seconds=60.0,
    )
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
        route_id="github:portal",
        queued_at=120.0,
    )

    first = store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="attempt-1",
        evidence_id="request:1",
        attempted_at=121.0,
    )
    replay = store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="attempt-1",
        evidence_id="request:1",
        attempted_at=200.0,
    )

    assert replay == first
    store.close()


def test_atomic_take_skips_expired_route_and_takes_next_current_dispatch(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    _advertise(
        store,
        subject_id="alpha",
        route_id="github:alpha",
        observed_at=100.0,
        ttl_seconds=20.0,
    )
    _advertise(
        store,
        subject_id="beta",
        route_id="github:beta",
        observed_at=100.0,
        ttl_seconds=100.0,
    )
    alpha = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="alpha",
        route_id="github:alpha",
        queued_at=110.0,
    )
    beta = _queue(
        store,
        run_id="portfolio::g2",
        subject_id="beta",
        route_id="github:beta",
        queued_at=111.0,
    )

    taken = store.take_pending_dispatch(
        session_id="portfolio",
        adapter_ids=("github",),
        attempt_id="attempt-beta",
        evidence_id="request:beta",
        attempted_at=130.0,
    )

    assert taken is not None
    assert taken["dispatch_id"] == beta
    assert taken["state"] == "ATTEMPTED"
    assert store.load_dispatch(alpha)["state"] == "QUEUED"
    store.close()


@pytest.mark.parametrize(
    "route_override",
    [
        {"available": False},
        {"attached": False},
        {"current": False},
        {"authorized_effects": ("NO_PROTECTED_EFFECT",)},
        {"effect_capabilities": ("NO_PROTECTED_EFFECT",)},
        {"capabilities": ("repository_read",)},
    ],
)
def test_attempt_revalidates_all_route_qualification_dimensions(
    tmp_path: Path,
    route_override: dict[str, object],
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    values = dict(
        adapter_id="github",
        route_id="github:portal",
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
    )
    values.update(route_override)
    store.advertise_route(
        PortalRouteAdvertisement(**values),
        observed_at=100.0,
        ttl_seconds=100.0,
    )
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
        route_id="github:portal",
        queued_at=110.0,
    )

    with pytest.raises(ValueError, match="route is not currently qualified"):
        store.mark_attempted(
            dispatch_id=dispatch_id,
            attempt_id="attempt-1",
            evidence_id="request:1",
            attempted_at=120.0,
        )
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()
