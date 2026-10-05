from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.runtime import PortalRunResult, PortalRunStore


ROOT = Path(__file__).resolve().parents[2]


def test_portal_run_once_cli_binds_runtime_configuration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return PortalRunResult(
            run_id=kwargs["run_id"],
            cycles=(),
            stop_reason="MAX_CYCLES",
            idle_cycles=0,
        )

    monkeypatch.setattr(portal_cli, "run_portal_until_idle", fake_run)

    code = portal_cli.entrypoint(
        [
            "run",
            "--once",
            "--projects",
            str(ROOT / "registry" / "projects.yaml"),
            "--workers",
            str(ROOT / "registry" / "workers.yaml"),
            "--dependencies",
            str(ROOT / "topology" / "dependencies.yaml"),
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "cli-run",
            "--max-parallel",
            "2",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_RUN_V1"
    assert payload["run_id"] == "cli-run"
    assert payload["stop_reason"] == "MAX_CYCLES"
    assert payload["protected_effects_authorized"] is False
    assert captured["max_cycles"] == 1
    assert captured["max_idle_cycles"] == 1
    assert captured["max_parallel"] == 2
    assert [node.node_id for node in captured["nodes"]] == [
        "lappy",
        "worklaptop",
    ]


def test_portal_status_cli_reads_durable_run(tmp_path: Path, capsys) -> None:
    db = tmp_path / "portal.sqlite3"
    store = PortalRunStore(db)
    try:
        store.ensure_run(
            run_id="status-run",
            config_digest="a" * 64,
            holder="vera",
            now=1.0,
        )
    finally:
        store.close()

    code = portal_cli.entrypoint(
        [
            "status",
            "--state-db",
            str(db),
            "--run-id",
            "status-run",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_RUN_STATUS_V1"
    assert payload["run"]["run_id"] == "status-run"
    assert payload["run"]["state"] == "READY"
    assert payload["run"]["cycles"] == 0
