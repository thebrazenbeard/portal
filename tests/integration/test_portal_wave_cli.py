from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.wave_runtime import (
    PortalWavePacket,
    PortalWavePreparationResult,
    PortalWaveStore,
)


ROOT = Path(__file__).resolve().parents[2]


def _packet() -> PortalWavePacket:
    return PortalWavePacket(
        run_id="wave-cli",
        subject_id="project-runner",
        repository="thebrazenbeard/project-runner",
        ref="main",
        exact_head="a" * 40,
        node_id="lappy",
        lane_id="ONE",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id="lineage",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="advance it",
        lead_identity="ONE",
        reviewer_identities=("REZON",),
    )


def test_portal_wave_prepare_cli_binds_budget_and_nodes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="b" * 64,
            plan_path=tmp_path / "plan.json",
            assigned=1,
            claimed=1,
            held=0,
            packets=(_packet(),),
        )

    monkeypatch.setattr(portal_cli, "prepare_portal_wave", fake_prepare)

    code = portal_cli.entrypoint(
        [
            "wave",
            "prepare",
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "wave-cli",
            "--max-parallel",
            "2",
            "--max-per-identity",
            "2",
            "--max-per-family",
            "2",
            "--max-per-lane",
            "2",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_PREPARE_V1"
    assert payload["run_id"] == "wave-cli"
    assert payload["assigned"] == 1
    assert payload["claimed"] == 1
    assert payload["protected_effects_authorized"] is False
    assert [node.node_id for node in captured["nodes"]] == [
        "lappy",
        "worklaptop",
    ]
    assert captured["budget"].max_parallel == 2


def test_portal_wave_status_cli_reads_durable_outbox(
    tmp_path: Path,
    capsys,
) -> None:
    db = tmp_path / "portal.sqlite3"
    store = PortalWaveStore(db)
    try:
        store.ensure_run(
            run_id="wave-status",
            config_digest="a" * 64,
            wave_sha256="b" * 64,
            plan_sha256="c" * 64,
            holder="vera",
            now=1.0,
        )
    finally:
        store.close()

    code = portal_cli.entrypoint(
        [
            "wave",
            "status",
            "--state-db",
            str(db),
            "--run-id",
            "wave-status",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_STATUS_V1"
    assert payload["wave"]["run_id"] == "wave-status"
    assert payload["wave"]["packets"] == 0
