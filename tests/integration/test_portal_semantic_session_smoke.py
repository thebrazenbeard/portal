from __future__ import annotations

import json
from pathlib import Path

from portal.cli import entrypoint

ROOT = Path(__file__).resolve().parents[2]


def test_advance_roundtrips_a_real_durable_session_without_effects(tmp_path: Path, capsys) -> None:
    """Separate Draft PR features must compose through a real SQLite session."""
    db = tmp_path / "portal.sqlite3"
    common = ["--session-id", "integration-smoke", "--state-db", str(db)]

    assert entrypoint([
        "run", *common, "--once", "--static-projects",
        "--nodes", str(ROOT / "tests/fixtures/portal-nodes-valid.yaml"),
        "--projects", str(ROOT / "tests/fixtures/projects-valid.yaml"),
    ]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["generation"] == 1
    assert started["protected_effects_authorized"] is False

    assert entrypoint(["advance", "advance my portfolio", *common, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "PORTAL_ADVANCE_PREVIEW_V1"
    assert preview["dispatched"] is False
    assert preview["status"]["generation"] == 1

    assert entrypoint(["advance", "advance my portfolio", *common]) == 0
    advanced = json.loads(capsys.readouterr().out)
    assert advanced["bounded_cycle"] is True
    assert advanced["result"]["generation"] == 2
    assert advanced["result"]["protected_effects_authorized"] is False

    assert entrypoint(["status", *common]) == 0
    persisted = json.loads(capsys.readouterr().out)
    assert persisted["generation"] == 2
    assert persisted["session_id"] == "integration-smoke"

