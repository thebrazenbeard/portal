from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from portal.node_registry import load_execution_nodes


ROOT = Path(__file__).resolve().parents[2]


def test_load_execution_nodes_normalizes_manifest() -> None:
    nodes = load_execution_nodes(
        ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"
    )

    assert [node.node_id for node in nodes] == ["lappy", "worklaptop"]
    assert [node.max_parallel for node in nodes] == [1, 1]


def test_load_execution_nodes_rejects_duplicate_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "nodes.yaml"
    manifest.write_text(
        """schema: PORTAL_EXECUTION_NODES_V1
nodes:
  - id: same
    max_parallel: 1
  - id: same
    max_parallel: 2
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate execution node id"):
        load_execution_nodes(manifest)


def test_load_execution_nodes_rejects_invalid_capacity(tmp_path: Path) -> None:
    manifest = tmp_path / "nodes.yaml"
    manifest.write_text(
        """schema: PORTAL_EXECUTION_NODES_V1
nodes:
  - id: bad
    max_parallel: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="execution node max_parallel must be a positive integer",
    ):
        load_execution_nodes(manifest)


def test_portal_plan_cli_emits_deterministic_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "portal.cli",
            "plan",
            "--wave",
            str(ROOT / "portfolio" / "advancement_wave.public.json"),
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--max-parallel",
            "2",
            "--max-per-lane",
            "2",
            "--max-per-identity",
            "2",
            "--max-per-family",
            "2",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "PORTAL_PLAN_V1"
    assert payload["summary"]["assignments"] <= 2
    assert payload["summary"]["node_deferrals"] >= 0
    assert set(payload["summary"]["assigned_by_node"]) == {
        "lappy",
        "worklaptop",
    }
    assert payload["assignments"] == sorted(
        payload["assignments"],
        key=lambda item: (item["subject_id"], item["node_id"]),
    )
