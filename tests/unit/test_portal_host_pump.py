from __future__ import annotations

from pathlib import Path

from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.host_pump import (
    PortalHostDriverResult,
    PortalHostPump,
)
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


def _packet(
    *,
    run_id: str,
    subject_id: str,
    adapter_id: str,
    route_id: str,
) -> tuple[PortalSessionResult, PortalWavePacket, PortalRouteBinding]:
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
        adapter_id=adapter_id,
        route_id=route_id,
    )
    return result, packet, binding


def _queue(
    store: PortalHostBridgeStore,
    *,
    run_id: str,
    subject_id: str,
    adapter_id: str = "github",
    route_id: str | None = None,
    queued_at: float = 100.0,
) -> str:
    if route_id is None:
        route_id = f"{adapter_id}:{subject_id}"
    result, packet, binding = _packet(
        run_id=run_id,
        subject_id=subject_id,
        adapter_id=adapter_id,
        route_id=route_id,
    )
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id=adapter_id,
            route_id=route_id,
            node_id=packet.node_id,
            target_kind="repository",
            target_id=packet.repository,
            capabilities=("semantic_work",),
            effect_capabilities=(packet.effect_ceiling,),
            authorized_effects=(packet.effect_ceiling,),
            available=True,
            attached=True,
            current=True,
            preference=50,
        ),
        observed_at=0.0,
        ttl_seconds=1_000_000_000_000.0,
    )
    queued = store.queue_dispatch(
        result=result,
        packet=packet,
        binding=binding,
        queued_at=queued_at,
    )
    return str(queued["dispatch_id"])


def test_pump_marks_attempt_before_driver_effect_and_records_completion(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
    )
    observed: list[str] = []

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            current = store.load_dispatch(dispatch["dispatch_id"])
            observed.append(str(current["state"]))
            assert attempt_id == "attempt-1"
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id="github:commit:verified",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: "attempt-1",
    )
    result = pump.run_once(session_id="portfolio")

    assert observed == ["ATTEMPTED"]
    assert result.attempted == 1
    assert result.verified_complete == 1
    final = store.load_dispatch(dispatch_id)
    assert final["state"] == "ATTEMPTED"
    assert final["reconciliation_state"] == "VERIFIED_COMPLETE"
    assert final["reconciliation_evidence_id"] == "github:commit:verified"
    store.close()


def test_driver_exception_leaves_attempted_dispatch_unresolved_and_not_replayed(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
    )
    calls = {"count": 0}

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            calls["count"] += 1
            raise RuntimeError("response lost after effect boundary")

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: "attempt-ambiguous",
    )

    first = pump.run_once(session_id="portfolio")
    second = pump.run_once(session_id="portfolio")

    assert calls["count"] == 1
    assert first.outcome_unknown == 1
    assert second.attempted == 0
    current = store.load_dispatch(dispatch_id)
    assert current["state"] == "ATTEMPTED"
    assert current["reconciliation_state"] is None
    unresolved = store.unresolved_dispatches(session_id="portfolio")
    assert [item["dispatch_id"] for item in unresolved] == [dispatch_id]
    store.close()


def test_missing_driver_leaves_dispatch_queued_without_crossing_effect_boundary(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
        adapter_id="workbridge",
    )

    pump = PortalHostPump(store=store, drivers={})
    result = pump.run_once(session_id="portfolio")

    assert result.attempted == 0
    assert result.no_driver == 1
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_unresolved_subject_does_not_block_unrelated_pending_dispatch(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    stuck = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="alpha",
    )
    ready = _queue(
        store,
        run_id="portfolio::g2",
        subject_id="beta",
    )
    store.mark_attempted(
        dispatch_id=stuck,
        attempt_id="alpha-attempt",
        evidence_id="portal-host-attempt:alpha-attempt",
        attempted_at=110.0,
    )

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            assert dispatch["dispatch_id"] == ready
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id="github:beta:verified",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: "beta-attempt",
    )
    result = pump.run_once(session_id="portfolio")

    assert result.attempted == 1
    assert result.verified_complete == 1
    assert store.load_dispatch(stuck)["reconciliation_state"] is None
    assert store.load_dispatch(ready)["reconciliation_state"] == "VERIFIED_COMPLETE"
    store.close()

def test_competing_host_attempt_is_skipped_without_driver_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
    )
    original = store.mark_attempted
    raced = {"done": False}
    calls = {"execute": 0}

    def competing_mark(**kwargs):
        if not raced["done"]:
            raced["done"] = True
            original(
                dispatch_id=dispatch_id,
                attempt_id="other-host",
                evidence_id="other-host:request",
            )
        return original(**kwargs)

    monkeypatch.setattr(store, "mark_attempted", competing_mark)

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            calls["execute"] += 1
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id="should-not-run",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: "this-host",
    )
    result = pump.run_once(session_id="portfolio")

    assert result.attempted == 0
    assert result.race_lost == 1
    assert calls["execute"] == 0
    current = store.load_dispatch(dispatch_id)
    assert current["state"] == "ATTEMPTED"
    assert current["attempt_id"] == "other-host"
    assert current["reconciliation_state"] is None
    store.close()


def test_pump_does_not_swallow_process_control_exceptions(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
    )

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            raise KeyboardInterrupt()

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: "interrupt-attempt",
    )

    try:
        pump.run_once(session_id="portfolio")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt must propagate")

    current = store.load_dispatch(dispatch_id)
    assert current["state"] == "ATTEMPTED"
    assert current["reconciliation_state"] is None
    store.close()

def test_stale_queued_route_does_not_block_unrelated_safe_dispatch(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    alpha = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="alpha",
        queued_at=90.0,
    )
    beta = _queue(
        store,
        run_id="portfolio::g2",
        subject_id="beta",
        queued_at=100.0,
    )

    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id="github",
            route_id="github:alpha",
            node_id="repo-native",
            target_kind="repository",
            target_id="thebrazenbeard/alpha",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True,
            attached=True,
            current=False,
            preference=50,
        ),
        observed_at=1.0,
        ttl_seconds=1_000_000_000_000.0,
    )

    executed: list[str] = []

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            executed.append(str(dispatch["subject_id"]))
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id=f"github:{dispatch['subject_id']}:verified",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: (
            "attempt:" + str(dispatch["subject_id"])
        ),
    )
    result = pump.run_once(session_id="portfolio")

    assert executed == ["beta"]
    assert result.route_unqualified == 1
    assert result.attempted == 1
    assert result.verified_complete == 1
    assert store.load_dispatch(alpha)["state"] == "QUEUED"
    assert store.load_dispatch(beta)["reconciliation_state"] == "VERIFIED_COMPLETE"
    store.close()

