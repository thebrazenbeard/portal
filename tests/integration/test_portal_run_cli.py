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



def test_portal_discover_cli_writes_live_registry_without_listing_names(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from portal.discovery import build_live_project_registry, RepositoryInventoryItem

    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            RepositoryInventoryItem(
                name="public-one",
                full_name="thebrazenbeard/public-one",
                private=False,
                archived=False,
                default_branch="main",
            ),
            RepositoryInventoryItem(
                name="private-one",
                full_name="thebrazenbeard/private-one",
                private=True,
                archived=False,
                default_branch="main",
            ),
        ),
        curated_projects=(),
    )

    def fake_discover(**kwargs):
        assert kwargs["owner"] == "thebrazenbeard"
        return snapshot

    monkeypatch.setattr(
        portal_cli,
        "discover_live_project_registry",
        fake_discover,
    )
    output = tmp_path / "projects.live.yaml"

    code = portal_cli.entrypoint(
        [
            "discover",
            "--owner",
            "thebrazenbeard",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_DISCOVERY_V1"
    assert payload["repositories"] == 2
    assert payload["public"] == 1
    assert payload["private"] == 1
    assert "public-one" not in json.dumps(payload)
    assert "private-one" not in json.dumps(payload)
    assert output.exists()


def test_portal_run_can_use_live_discovered_registry(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from portal.discovery import build_live_project_registry, RepositoryInventoryItem

    captured = {}
    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            RepositoryInventoryItem(
                name="project-runner",
                full_name="thebrazenbeard/project-runner",
                private=False,
                archived=False,
                default_branch="main",
            ),
            RepositoryInventoryItem(
                name="chat-communication-bus",
                full_name="thebrazenbeard/chat-communication-bus",
                private=True,
                archived=False,
                default_branch="main",
            ),
            RepositoryInventoryItem(
                name="vera",
                full_name="thebrazenbeard/vera",
                private=True,
                archived=False,
                default_branch="main",
            ),
            RepositoryInventoryItem(
                name="vera-control-plane",
                full_name="thebrazenbeard/vera-control-plane",
                private=True,
                archived=False,
                default_branch="main",
            ),
        ),
        curated_projects=(),
    )

    monkeypatch.setattr(
        portal_cli,
        "discover_live_project_registry",
        lambda **_kwargs: snapshot,
    )

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
            "--discover-owner",
            "thebrazenbeard",
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "live-run",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "live-run"
    assert len(captured["projects"]) == 4
