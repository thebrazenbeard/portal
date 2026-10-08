"""Contract tests; no Ollama server, paid API, or GitHub connection required."""
from pathlib import Path
import json
import subprocess

import pytest

from portal import local_ollama_worker as worker
from portal.source_proposal import load_source_tree_proposal


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _seed(tmp_path):
    checkout = tmp_path / "source"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    _git(checkout, "config", "user.email", "tests@example.invalid")
    _git(checkout, "config", "user.name", "Test Only")
    (checkout / "README.md").write_text("# Bounded source review\n", encoding="utf-8")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-qm", "fixture")
    _git(checkout, "remote", "add", "origin",
         "https://github.com/thebrazenbeard/firesafe.git")
    packet = {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "repository": "thebrazenbeard/firesafe",
        "source_ref": "main",
        "exact_head": _git(checkout, "rev-parse", "HEAD"),
        "effect_ceiling": "SOURCE_ONLY",
        "advisory_only": True,
        "execution_authorized": False,
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    return checkout, packet, packet_path


def test_packet_to_sha_bound_proposal_is_source_only(tmp_path, monkeypatch):
    checkout, packet, packet_path = _seed(tmp_path)
    monkeypatch.setattr(worker, "_query_local_model", lambda *args: {
        "observations": ["README identifies a bounded source review."],
        "suggested_check": "Verify the README at the exact head.",
        "uncertainty": "No independent code execution was performed.",
    })
    receipt_path = tmp_path / "artifacts" / "receipt.json"
    worker.run(packet_path, receipt_path, checkout, "local-test-model")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_class"] == "PROPOSED_SOURCE_TREE"
    artifact = receipt_path.parent / "proposal.json"
    proposal = load_source_tree_proposal(artifact, packet=packet)
    assert proposal.expected_head == packet["exact_head"]
    assert len(proposal.files) == 1
    assert proposal.files[0].path == worker.OUTPUT_PATH
    assert _git(checkout, "status", "--porcelain") == ""
    assert "Advisory proposal only" in proposal.files[0].content


def test_rejects_protected_effect_packet_before_model(tmp_path, monkeypatch):
    checkout, packet, packet_path = _seed(tmp_path)
    packet["source_mutation_authorized"] = True
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    monkeypatch.setattr(worker, "_query_local_model",
                        lambda *args: pytest.fail("untrusted packet reached model"))
    with pytest.raises(ValueError, match="does not deny"):
        worker.run(packet_path, tmp_path / "receipt.json", checkout, "test")
    assert not (tmp_path / "receipt.json").exists()


def test_rejects_stale_or_dirty_checkout_before_model(tmp_path, monkeypatch):
    checkout, packet, packet_path = _seed(tmp_path)
    monkeypatch.setattr(worker, "_query_local_model",
                        lambda *args: pytest.fail("stale checkout reached model"))
    packet["exact_head"] = "a" * 40
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(ValueError, match="stale checkout"):
        worker.run(packet_path, tmp_path / "receipt.json", checkout, "test")
    packet["exact_head"] = _git(checkout, "rev-parse", "HEAD")
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    (checkout / "untracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="unclean checkout"):
        worker.run(packet_path, tmp_path / "receipt.json", checkout, "test")


def test_rejects_hidden_execution_promotion(tmp_path, monkeypatch):
    checkout, packet, packet_path = _seed(tmp_path)
    packet["execution_promotion"] = {"execution_grant_sha256": "a" * 64}
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    monkeypatch.setattr(worker, "_query_local_model",
                        lambda *args: pytest.fail("promotion reached model"))
    with pytest.raises(ValueError, match="promotion"):
        worker.run(packet_path, tmp_path / "receipt.json", checkout, "test")


def test_temporary_local_model_outage_is_retryable(tmp_path, monkeypatch):
    from urllib.error import URLError
    checkout, packet, packet_path = _seed(tmp_path)
    monkeypatch.setattr(worker, "_query_local_model",
                        lambda *args: (_ for _ in ()).throw(URLError("offline")))
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr("sys.argv", ["local_ollama_worker",
                        "--portal-packet", str(packet_path),
                        "--portal-receipt", str(receipt),
                        "--checkout", str(checkout)])
    assert worker.main() == 1
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["receipt_class"] == "FAILED_RETRYABLE"
