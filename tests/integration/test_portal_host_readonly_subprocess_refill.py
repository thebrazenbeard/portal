"""Full native-session refill through a real subprocess and read-only driver.

The synthetic admission provider isolates the boundary under test.
The resident session, SQLite route/attempt/reconcile/refill, subprocess protocol,
and packaged driver are not mocked. The child does not call the network.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import portal.session as portal_session
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.host_command_driver import PortalCommandHostDriver
from portal.host_pump import PortalHostPump
from portal.models import ExecutionNode
from portal.route_resolver import PortalRouteAdvertisement
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _packet(run_id: str, name: str) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=name,
        repository=f"thebrazenbeard/{name}",
        ref="main",
        exact_head="a" * 40,
        node_id="lappy",
        lane_id="read-only-proof",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"proof:{name}",
        work_fingerprint=("c" if name == "alpha" else "d") * 64,
        action="VERIFY_EXACT_HEAD",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="Read-only ref verification",
        lead_identity="vera",
        reviewer_identities=(),
    )


def test_native_refill_uses_real_packaged_driver_process_and_durable_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    # This separate process imports the real packaged module, then patches only
    # its owning GitHub-GET transport so offline CI never needs credentials.
    wrapper = tmp_path / "isolated_child.py"
    wrapper.write_text(
        "from portal import host_github_ref_driver as driver\n"
        "driver.read_live_head = lambda repo, ref: 'a' * 40\n"
        "raise SystemExit(driver.main([\n"
        "  '--allowed-repository', 'thebrazenbeard/alpha',\n"
        "  '--allowed-repository', 'thebrazenbeard/beta',\n"
        "  '--adapter-id', 'lappy-local-readonly-gh',\n"
        "  '--node-id', 'lappy',\n"
        "]))\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(repo_root), existing] if existing else [str(repo_root)]),
    )
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    selected: list[str] = []

    def synthetic_prepare(**kwargs):
        excluded = set(kwargs["excluded_subjects"])
        active = set(kwargs["active_subjects"])
        remaining = [
            name for name in ("alpha", "beta")
            if ("repository", name) not in excluded
            and ("repository", name) not in active
        ]
        packets = (
            (_packet(kwargs["run_id"], remaining[0]),)
            if remaining and not active else ()
        )
        selected.extend(p.subject_id for p in packets)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"], plan_sha256="b" * 64,
            plan_path=tmp_path / "plan.json", assigned=len(packets),
            claimed=len(packets), held=0, packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", synthetic_prepare)

    db = tmp_path / "native-refill.sqlite3"
    store = PortalHostBridgeStore(db)
    for name in ("alpha", "beta"):
        store.advertise_route(
            PortalRouteAdvertisement(
                adapter_id="lappy-local-readonly-gh",
                route_id=f"local-gh:{name}:main",
                node_id="lappy",
                target_kind="repository",
                target_id=f"thebrazenbeard/{name}",
                capabilities=("semantic_work",),
                effect_capabilities=("SOURCE_ONLY",),
                authorized_effects=("SOURCE_ONLY",),
                available=True, attached=True, current=True, preference=50,
            ),
            ttl_seconds=600.0,
        )
    adapter = PortalHostExecutionAdapter(store=store)
    real_command_driver = PortalCommandHostDriver(
        command=(sys.executable, "-B", str(wrapper)),
        cwd=repo_root, timeout_seconds=20.0,
    )
    pump = PortalHostPump(
        store=store,
        drivers={"lappy-local-readonly-gh": real_command_driver},
        attempt_id_factory=lambda item: "ci-process:" + str(item["subject_id"]),
    )
    observed = []

    def between_cycles():
        result = pump.run_once(session_id="native-process-proof", max_dispatches=1)
        observed.append(result)
        return result

    controller = portal_session.PortalCommandSession(db)
    result = controller.run_until_idle(
        session_id="native-process-proof",
        holder="vera",
        wave_path=tmp_path / "unused-wave.json",
        corpus_path=tmp_path / "unused-corpus.json",
        projects_path=tmp_path / "unused-projects.yaml",
        nodes=(ExecutionNode(node_id="lappy", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        verifier="verification",
        max_cycles=8, max_idle_cycles=0, poll_seconds=0.0,
        execution_adapter=adapter, between_cycles=between_cycles,
    )
    assert result.stop_reason == "IDLE"
    assert result.summary == {"active": 0, "held": 0, "terminal": 2}
    assert selected == ["alpha", "beta"]
    assert len(result.cycles) >= 3
    assert sum(x.verified_complete for x in observed) == 2
    assert store.pending_dispatches(session_id="native-process-proof") == ()
    assert store.unresolved_dispatches(session_id="native-process-proof") == ()
    assert pump.run_once(session_id="native-process-proof").attempted == 0
    controller.close()
    store.close()

    reopened = PortalHostBridgeStore(db)
    rows = reopened.connection.execute(
        "SELECT subject_id, state, attempt_id, reconciliation_state, "
        "reconciliation_evidence_id FROM portal_host_dispatches"
    ).fetchall()
    assert len(rows) == 2
    for name, state, attempt, reconciled, evidence in rows:
        assert name in ("alpha", "beta")
        assert state == "ATTEMPTED"
        assert attempt == f"ci-process:{name}"
        assert reconciled == "VERIFIED_COMPLETE"
        assert evidence == f"github-readonly-ref:thebrazenbeard/{name}:" + "a" * 40
    reopened.close()