from __future__ import annotations

from pathlib import Path

from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


def _packet(run_id: str, subject_id: str) -> PortalWavePacket:
    return PortalWavePacket(
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
        work_fingerprint=("c" if subject_id == "alpha" else "d") * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Advance {subject_id}.",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _result(run_id: str, packet: PortalWavePacket) -> PortalSessionResult:
    return PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id=run_id,
        packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )


def _queue(store: PortalHostBridgeStore, subject_id: str, queued_at: float) -> str:
    run_id = f"portfolio::{subject_id}"
    packet = _packet(run_id, subject_id)
    binding = PortalRouteBinding(
        subject_kind="repository",
        subject_id=subject_id,
        adapter_id="github",
        route_id=f"github:{subject_id}",
    )
    record = store.queue_dispatch(
        result=_result(run_id, packet),
        packet=packet,
        binding=binding,
        queued_at=queued_at,
    )
    return str(record["dispatch_id"])


def _advertise(
    store: PortalHostBridgeStore,
    subject_id: str,
    *,
    current: bool,
) -> None:
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id="github",
            route_id=f"github:{subject_id}",
            node_id="repo-native",
            target_kind="repository",
            target_id=f"thebrazenbeard/{subject_id}",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True,
            attached=True,
            current=current,
            preference=50,
        ),
        observed_at=100.0,
        ttl_seconds=1_000.0,
    )


def test_pending_dispatch_diagnostics_classify_stale_routes_without_mutation(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    alpha = _queue(store, "alpha", 90.0)
    beta = _queue(store, "beta", 91.0)
    _advertise(store, "alpha", current=False)
    _advertise(store, "beta", current=True)

    diagnostics = store.pending_dispatch_diagnostics(
        session_id="portfolio",
        now=200.0,
    )

    assert [
        (item["subject_id"], item["route_qualified"])
        for item in diagnostics
    ] == [
        ("alpha", False),
        ("beta", True),
    ]
    assert store.load_dispatch(alpha)["state"] == "QUEUED"
    assert store.load_dispatch(beta)["state"] == "QUEUED"
    store.close()
