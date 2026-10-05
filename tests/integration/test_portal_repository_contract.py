from __future__ import annotations

import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
EXPANSION = "Portfolio Orchestration & Repository Tracking Access Layer"
SOURCE_COMMIT = "04702abbf51aa2920b7d054275619253ea6fa748"
SOURCE_TREE = "f3f228258bc2de73724c598721bd5aa5beedab95"


def test_portal_identity_and_bootstrap_provenance_are_explicit() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    portal_doc = (ROOT / "PORTAL.md").read_text(encoding="utf-8")
    bootstrap = json.loads(
        (ROOT / ".portal-bootstrap.json").read_text(encoding="utf-8")
    )

    assert "# P.O.R.T.A.L." in readme
    assert EXPANSION in readme
    assert EXPANSION in portal_doc
    assert "Project Runner remains the governed execution substrate" in portal_doc
    assert "does not grant execution authority" in portal_doc

    assert bootstrap["name"] == "P.O.R.T.A.L."
    assert bootstrap["expansion"] == EXPANSION
    assert bootstrap["source_repository"] == "thebrazenbeard/project-runner"
    assert bootstrap["source_commit"] == SOURCE_COMMIT
    assert bootstrap["source_tree"] == SOURCE_TREE


def test_both_portal_and_project_runner_clis_remain_packaged() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]

    assert scripts["portal"] == "portal.cli:entrypoint"
    assert scripts["project-runner"] == "runner.cli:entrypoint"
