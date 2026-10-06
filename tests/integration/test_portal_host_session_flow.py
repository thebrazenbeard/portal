from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
import portal.session as portal_session
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult


def _packet(
    *,
    run_id: str,
    subject_id: str,
    repository: str,
) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=repository,
        ref="work/frontier",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage:{subject_id}",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Advance {subject_id}.",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _advertise_route(
    *,
    state_db: Path,
    target_id: str,
) -> None:
    code = portal_cli.entrypoint([
        "host", "advertise",
        "--state-db", str(state_db),
        "--adapter-id", "github",
        "--route-id", f"repo-native:{target_id}",
        "--node-id", "repo-native",
        "--target-id", target_id,
        "--capability", "semantic_work",
        "--effect-capability", "SOURCE_ONLY",
        "--authorized-effect", "SOURCE_ONLY",
        "--ttl-seconds", "300",
    ])
    assert code == 0


def test_cli_host_bridge_reconciles_and_refills_next_repository(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    prepare_calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        assert kwargs["occupied_node_slots"] == {"repo-native": 0}
        if len(prepare_calls) == 1:
            packets = (
                _packet(
                    run_id=kwargs["run_id"],
                    subject_id="project-runner",
                    repository="thebrazenbeard/project-runner",
                ),
            )
        elif len(prepare_calls) == 2:
            assert kwargs["active_subjects"] == ()
            assert kwargs["excluded_subjects"] == (
                ("repository", "project-runner"),
            )
            packets = (
                _packet(
                    run_id=kwargs["run_id"],
                    subject_id="lou-pole",
                    repository="thebrazenbeard/lou-pole",
                ),
            )
        else:
            packets = ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    nodes = tmp_path / "nodes.yaml"
    nodes.write_text(
        "schema: PORTAL_EXECUTION_NODES_V1\n"
        "nodes:\n"
        "  - id: repo-native\n"
        "    max_parallel: 1\n"
        "    allowed_lanes: []\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    state_db = tmp_path / "portal.sqlite3"

    _advertise_route(
        state_db=state_db,
        target_id="thebrazenbeard/project-runner",
    )
    capsys.readouterr()
    _advertise_route(
        state_db=state_db,
        target_id="thebrazenbeard/lou-pole",
    )
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "occupancy",
        "--state-db", str(state_db),
        "--node-id", "repo-native",
        "--occupied-slots", "0",
        "--ttl-seconds", "300",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(state_db),
        "--nodes", str(nodes),
        "--host-bridge",
        "--host-node-occupancy",
        "--once",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "pending",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    pending = json.loads(capsys.readouterr().out)["dispatches"]
    assert [item["subject_id"] for item in pending] == ["project-runner"]
    dispatch_id = pending[0]["dispatch_id"]

    assert portal_cli.entrypoint([
        "host", "attempt",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--attempt-id", "github-project-runner-1",
        "--evidence-id", "github:request:project-runner:1",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "reconcile",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--state", "VERIFIED_COMPLETE",
        "--evidence-id", "github:commit:project-runner",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--state-db", str(state_db),
        "--nodes", str(nodes),
        "--host-bridge",
        "--host-node-occupancy",
        "--verifier", "vera-review",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "pending",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    pending = json.loads(capsys.readouterr().out)["dispatches"]
    assert [item["subject_id"] for item in pending] == ["lou-pole"]

    assert portal_cli.entrypoint([
        "status",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["summary"] == {"active": 1, "held": 0, "terminal": 1}

def test_host_reconcile_immediately_updates_existing_session_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def fake_prepare(**kwargs):
        packet = _packet(
            run_id=kwargs["run_id"],
            subject_id="project-runner",
            repository="thebrazenbeard/project-runner",
        )
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=1,
            claimed=1,
            held=0,
            packets=(packet,),
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    nodes = tmp_path / "nodes.yaml"
    nodes.write_text(
        "schema: PORTAL_EXECUTION_NODES_V1\n"
        "nodes:\n"
        "  - id: repo-native\n"
        "    max_parallel: 1\n"
        "    allowed_lanes: []\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    state_db = tmp_path / "portal.sqlite3"

    _advertise_route(
        state_db=state_db,
        target_id="thebrazenbeard/project-runner",
    )
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(state_db),
        "--nodes", str(nodes),
        "--host-bridge",
        "--occupied-node", "repo-native=0",
        "--once",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "pending",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    pending = json.loads(capsys.readouterr().out)["dispatches"]
    dispatch_id = pending[0]["dispatch_id"]

    assert portal_cli.entrypoint([
        "host", "attempt",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--attempt-id", "project-runner-hold-1",
        "--evidence-id", "host:attempt:project-runner-hold-1",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "host", "reconcile",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--state", "VERIFIED_HELD",
        "--evidence-id", "host:held:project-runner",
    ]) == 0
    capsys.readouterr()

    assert portal_cli.entrypoint([
        "status",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["summary"] == {"active": 0, "held": 1, "terminal": 0}
    assert status["subjects"][0]["state"] == "HELD"
    assert status["subjects"][0]["verification_state"] == "VERIFIED_HELD"
    assert (
        status["subjects"][0]["dispatch_evidence_id"]
        == "host:held:project-runner"
    )

