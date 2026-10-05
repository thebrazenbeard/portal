from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from portal.source_proposal import (
    load_source_tree_proposal,
    source_tree_proposal_to_execution_request,
)


PACKET = {
    "schema": "PORTAL_WAVE_WORK_PACKET_V1",
    "run_id": "run-1",
    "subject_id": "repo-a",
    "repository": "example/repo-a",
    "source_ref": "main",
    "exact_head": "a" * 40,
    "node_id": "alpha",
    "lane_id": "LANE_A",
    "action": "EXECUTE_FRONTIER",
    "frontier": "advance repo-a",
    "effect_ceiling": "SOURCE_ONLY",
    "review_gate": "EXACT_HEAD_REVIEW",
    "lead_identity": "vera",
    "reviewer_identities": ["reviewer"],
    "plan_sha256": "b" * 64,
    "project_runner_claim": {
        "fencing_token": 1,
        "lineage_id": "lineage",
        "work_fingerprint": "c" * 64,
    },
    "delivery_fencing_token": 1,
    "execution_authorized": False,
    "protected_effects_authorized": False,
    "source_mutation_authorized": False,
    "target_ref_mutation_authorized": False,
}


def _proposal():
    return {
        "schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
        "repository": "example/repo-a",
        "source_ref": "main",
        "expected_head": "a" * 40,
        "message": "Implement bounded frontier",
        "files": [
            {
                "path": "src/a.py",
                "content": "A = 1\n",
                "expected_blob_sha": None,
            },
            {
                "path": "tests/test_a.py",
                "content": "def test_a(): assert True\n",
                "expected_blob_sha": "d" * 40,
            },
        ],
    }


def test_source_tree_proposal_is_bound_to_exact_worker_packet(tmp_path: Path):
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(_proposal()), encoding="utf-8")

    proposal = load_source_tree_proposal(path, packet=PACKET)

    assert proposal.repository == "example/repo-a"
    assert proposal.source_ref == "main"
    assert proposal.expected_head == "a" * 40
    assert [item.path for item in proposal.files] == [
        "src/a.py",
        "tests/test_a.py",
    ]
    assert proposal.sha256 == hashlib.sha256(proposal.canonical_bytes).hexdigest()

    request = source_tree_proposal_to_execution_request(proposal)
    assert request["schema"] == "PROJECT_RUNNER_GITHUB_SOURCE_TREE_WRITE_V1"
    assert request["operation"] == "PUT_FILES"
    assert request["expected_head"] == "a" * 40
    assert len(request["files"]) == 2


def test_source_tree_proposal_rejects_packet_target_divergence(tmp_path: Path):
    raw = _proposal()
    raw["repository"] = "example/other"
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="repository diverges"):
        load_source_tree_proposal(path, packet=PACKET)


def test_source_tree_proposal_rejects_duplicate_and_noncanonical_paths(
    tmp_path: Path,
):
    raw = _proposal()
    raw["files"].append(dict(raw["files"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_source_tree_proposal(path, packet=PACKET)

    raw = _proposal()
    raw["files"][0]["path"] = "../escape.py"
    path = tmp_path / "escape.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical"):
        load_source_tree_proposal(path, packet=PACKET)


def test_source_tree_proposal_rejects_bad_expected_blob(tmp_path: Path):
    raw = _proposal()
    raw["files"][0]["expected_blob_sha"] = "not-a-sha"
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="expected blob"):
        load_source_tree_proposal(path, packet=PACKET)
