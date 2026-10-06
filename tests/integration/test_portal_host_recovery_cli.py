from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


def _queue_attempted(state_db: Path) -> str:
    store = PortalHostBridgeStore(state_db)
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
        ttl_seconds=300.0,
    )
    packet = PortalWavePacket(
        run_id="portfolio::g1",
        subject_id="portal",
        repository="thebrazenbeard/portal",
        ref="work/frontier",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id="lineage:portal",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="Advance portal.",
        lead_identity="vera",
        reviewer_identities=(),
    )
    result = PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id="portfolio::g1",
        packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    adapter = PortalHostExecutionAdapter(store=store)
    binding = adapter.select_routes(result)
    record = adapter.dispatch(result, binding)[0]
    dispatch_id = record.evidence_id.removeprefix("host-dispatch:")
    store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
    )
    store.close()
    return dispatch_id


def test_attempted_host_effect_is_recoverable_after_it_leaves_pending(
    tmp_path: Path,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    dispatch_id = _queue_attempted(state_db)

    assert portal_cli.entrypoint([
        "host", "pending",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["dispatches"] == []

    assert portal_cli.entrypoint([
        "host", "unresolved",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    unresolved = json.loads(capsys.readouterr().out)
    assert unresolved["mode"] == "PORTAL_HOST_UNRESOLVED_V1"
    assert len(unresolved["dispatches"]) == 1
    item = unresolved["dispatches"][0]
    assert item["dispatch_id"] == dispatch_id
    assert item["state"] == "ATTEMPTED"
    assert item["attempt_id"] == "github-call-1"
    assert item["dispatch_evidence_id"] == "github:request:1"
    assert item["reconciliation_state"] is None


def test_outcome_unknown_remains_unresolved_until_verified_terminal(
    tmp_path: Path,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    dispatch_id = _queue_attempted(state_db)

    assert portal_cli.entrypoint([
        "host", "reconcile",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--state", "OUTCOME_UNKNOWN",
        "--evidence-id", "github:unknown:1",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "unresolved",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    unresolved = json.loads(capsys.readouterr().out)["dispatches"]
    assert len(unresolved) == 1
    assert unresolved[0]["reconciliation_state"] == "OUTCOME_UNKNOWN"
    assert unresolved[0]["reconciliation_evidence_id"] == "github:unknown:1"

    assert portal_cli.entrypoint([
        "host", "reconcile",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--state", "VERIFIED_COMPLETE",
        "--evidence-id", "github:commit:verified",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "unresolved",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["dispatches"] == []
