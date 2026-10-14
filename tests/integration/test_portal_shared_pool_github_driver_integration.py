"""Integration contract: shared-pool claims + packaged GitHub ref subprocess.

Only the remote GitHub transport is substituted for offline determinism.
Queue, currentness, attempt custody, protocol, and durable reconciliation are real.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.host_command_driver import PortalCommandHostDriver
from portal.host_pump import PortalHostPump
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket

ROOT = Path(__file__).resolve().parents[2]


def _queue(store: PortalHostBridgeStore, *, name: str, expected: str, adapter: str) -> str:
    packet = PortalWavePacket(
        run_id="portfolio::g1",
        subject_id=name,
        repository=f"thebrazenbeard/{name}",
        ref="main",
        exact_head=expected,
        node_id="lappy",
        lane_id="read-only-proof",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"proof:{name}",
        work_fingerprint="c" * 64,
        action="VERIFY_EXACT_HEAD",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Verify {name} exact head",
        lead_identity="vera",
        reviewer_identities=(),
    )
    route = f"local-gh:{name}:main"
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id=adapter,
            route_id=route,
            node_id="lappy",
            target_kind="repository",
            target_id=packet.repository,
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True, attached=True, current=True, preference=50,
        ),
        ttl_seconds=600.0,
    )
    session = PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id=packet.run_id,
        packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    binding = PortalRouteBinding(
        subject_kind="repository", subject_id=name,
        adapter_id=adapter, route_id=route,
    )
    return str(store.queue_dispatch(result=session, packet=packet, binding=binding)["dispatch_id"])


def test_shared_pool_subprocess_verifies_matching_head_holds_stale_and_preserves_other_adapter(
    tmp_path: Path, monkeypatch,
) -> None:
    # Child executes the packaged driver and validates its full envelope.
    # Replace only the provider's external head-read with deterministic data.
    wrapper = tmp_path / "github_transport_fixture.py"
    wrapper.write_text(
        "from portal import host_github_ref_driver as d\n"
        "d.read_live_head = lambda repo, ref: 'a' * 40\n"
        "raise SystemExit(d.main([\n"
        "  '--allowed-repository', 'thebrazenbeard/alpha',\n"
        "  '--allowed-repository', 'thebrazenbeard/beta',\n"
        "  '--adapter-id', 'lappy-local-readonly-gh',\n"
        "  '--node-id', 'lappy',\n"
        "]))\n",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(ROOT), existing] if existing else [str(ROOT)]),
    )
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    path = tmp_path / "pool.sqlite3"
    store = PortalHostBridgeStore(path)
    matched = _queue(store, name="alpha", expected="a" * 40, adapter="lappy-local-readonly-gh")
    stale = _queue(store, name="beta", expected="b" * 40, adapter="lappy-local-readonly-gh")
    other = _queue(store, name="gamma", expected="a" * 40, adapter="unavailable-host")
    driver = PortalCommandHostDriver(
        command=(sys.executable, "-B", str(wrapper)),
        cwd=ROOT, timeout_seconds=15.0,
    )
    pump = PortalHostPump(store=store, drivers={"lappy-local-readonly-gh": driver})
    result = pump.run_shared_pool_once(session_id="portfolio", max_dispatches=3)

    assert result.attempted == 2
    assert result.verified_complete == 1
    assert result.verified_held == 1
    assert result.outcome_unknown == 0
    assert store.load_dispatch(matched)["reconciliation_state"] == "VERIFIED_COMPLETE"
    assert store.load_dispatch(stale)["reconciliation_state"] == "VERIFIED_HELD"
    assert store.load_dispatch(other)["state"] == "QUEUED"
    assert pump.run_shared_pool_once(session_id="portfolio", max_dispatches=3).attempted == 0
    store.close()

    reopened = PortalHostBridgeStore(path)
    for dispatch_id, state in ((matched, "VERIFIED_COMPLETE"), (stale, "VERIFIED_HELD")):
        row = reopened.load_dispatch(dispatch_id)
        assert row["state"] == "ATTEMPTED"
        assert row["attempt_id"]
        assert row["reconciliation_state"] == state
        assert row["reconciliation_evidence_id"].endswith("a" * 40)
    assert reopened.load_dispatch(other)["state"] == "QUEUED"
    reopened.close()
