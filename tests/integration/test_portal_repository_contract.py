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


def test_whole_portfolio_host_interface_mission_is_architectural() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    portal_doc = (ROOT / "PORTAL.md").read_text(encoding="utf-8")
    architecture = (
        ROOT
        / "docs"
        / "architecture"
        / "PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md"
    ).read_text(encoding="utf-8")
    donors = (
        ROOT
        / "docs"
        / "research"
        / "PORTAL_LOOP_DONOR_MINING_V1.md"
    ).read_text(encoding="utf-8")

    assert "whole-portfolio orchestration and host interface layer" in readme
    assert "ChatGPT can be one interaction surface" in readme
    assert "not a runtime dependency" in readme

    assert "whole-portfolio orchestration and host interface layer" in portal_doc
    assert "one interaction surface" in portal_doc
    assert "not canonical persistence" in portal_doc
    assert "parallel" in portal_doc.lower()

    assert "single P.O.R.T.A.L. chat" in architecture
    assert "whole portfolio" in architecture
    assert "parallel" in architecture.lower()

    assert "Vera" in architecture
    assert "Project Runner" in architecture
    assert "portfolio-cycle" in architecture
    assert "consume-queue" in architecture
    assert "claim-worker-route" in architecture
    assert "record-worker-receipt" in architecture
    assert "task-start" in architecture
    assert "pre-active" in donors
    assert "wip" in donors
    assert "ccb-core" in donors
    assert "vera-mesh" in donors
    assert "workbridgecommander" in donors
    assert "LongHorizon-Harness" in donors
    assert "inferable" in donors
    assert "bmalph" in donors

    assert (
        "mine Patrick-owned repositories before adopting external mechanisms"
        in donors
    )


def test_both_portal_and_project_runner_clis_remain_packaged() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]

    assert scripts["portal"] == "portal.cli:entrypoint"
    assert scripts["project-runner"] == "runner.cli:entrypoint"
