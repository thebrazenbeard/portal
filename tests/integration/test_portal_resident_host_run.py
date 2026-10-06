from __future__ import annotations

from pathlib import Path

import portal.session as portal_session
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.host_pump import PortalHostDriverResult, PortalHostPump
from portal.models import ExecutionNode
from portal.route_resolver import PortalRouteAdvertisement
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult
from runner.portfolio_wave_scheduler import WaveExecutionBudget


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


def test_resident_between_cycle_hook_pumps_and_refills_with_zero_poll_delay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        excluded = set(kwargs["excluded_subjects"])
        active = set(kwargs["active_subjects"])
        packets: tuple[PortalWavePacket, ...] = ()
        if not active:
            if ("repository", "alpha") not in excluded:
                packets = (_packet(kwargs["run_id"], "alpha"),)
            elif ("repository", "beta") not in excluded:
                packets = (_packet(kwargs["run_id"], "beta"),)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="e" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    for subject_id in ("alpha", "beta"):
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
                current=True,
                preference=50,
            ),
            observed_at=0.0,
            ttl_seconds=1_000_000_000_000.0,
        )

    adapter = PortalHostExecutionAdapter(store=store)
    executed: list[str] = []

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            subject_id = str(dispatch["subject_id"])
            executed.append(subject_id)
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id=f"github:{subject_id}:verified",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: (
            "attempt:" + str(dispatch["subject_id"])
        ),
    )

    controller = portal_session.PortalCommandSession(state_db)
    result = controller.run_until_idle(
        session_id="portfolio",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="repo-native", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        verifier="vera-review",
        max_cycles=6,
        max_idle_cycles=0,
        poll_seconds=0.0,
        execution_adapter=adapter,
        between_cycles=lambda: pump.run_once(session_id="portfolio"),
    )

    assert executed == ["alpha", "beta"]
    assert result.stop_reason == "IDLE"
    assert result.summary == {"active": 0, "held": 0, "terminal": 2}
    assert store.pending_dispatches(session_id="portfolio") == ()
    assert store.unresolved_dispatches(session_id="portfolio") == ()
    assert prepare_calls[2]["excluded_subjects"] == (
        ("repository", "alpha"),
    )

    controller.close()
    store.close()
