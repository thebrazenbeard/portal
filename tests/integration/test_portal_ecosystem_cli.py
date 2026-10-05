from __future__ import annotations

import json
from pathlib import Path
import sys

import portal.cli as portal_cli
from portal.ecosystem_runtime import PortalEcosystemResult


ROOT = Path(__file__).resolve().parents[2]


def test_ecosystem_propose_cli_runs_parent_refill_session(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    monkeypatch.setattr(
        portal_cli,
        "load_worker_backends",
        lambda _path: {},
    )

    def fake_run(**kwargs):
        captured.update(kwargs)
        return PortalEcosystemResult(
            session_id=kwargs["session_id"],
            generations=3,
            admitted=6,
            awaiting_promotion=6,
            active=6,
            terminal=0,
            duplicate_admissions=0,
            workstreams_remaining=2,
            saturated=False,
        )

    monkeypatch.setattr(
        portal_cli,
        "run_ecosystem_proposal_generations",
        fake_run,
    )

    code = portal_cli.entrypoint(
        [
            "ecosystem",
            "propose",
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--worker-backends",
            str(tmp_path / "workers.yaml"),
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--workspace-root",
            str(tmp_path / "workspaces"),
            "--session-id",
            "all-projects",
            "--max-generations",
            "3",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_ECOSYSTEM_PROPOSE_V1"
    assert payload["session_id"] == "all-projects"
    assert payload["generations"] == 3
    assert payload["admitted"] == 6
    assert payload["awaiting_promotion"] == 6
    assert payload["workstreams_remaining"] == 2
    assert payload["protected_effects_authorized"] is False
    assert captured["max_generations"] == 3


def test_ecosystem_status_cli_reads_parent_session(
    tmp_path: Path,
    capsys,
) -> None:
    from portal.ecosystem_runtime import PortalEcosystemStore

    db = tmp_path / "portal.sqlite3"
    store = PortalEcosystemStore(db)
    try:
        store.ensure_session(
            session_id="all-projects",
            config_digest="a" * 64,
            now=1.0,
        )
    finally:
        store.close()

    code = portal_cli.entrypoint(
        [
            "ecosystem",
            "status",
            "--state-db",
            str(db),
            "--session-id",
            "all-projects",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_ECOSYSTEM_STATUS_V1"
    assert payload["ecosystem"]["session_id"] == "all-projects"
    assert payload["ecosystem"]["generations"] == 0
