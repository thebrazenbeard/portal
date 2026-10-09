"""Resident revalidation closes supervisor-to-host manifest drift, not OS TOCTOU."""
import hashlib
import json
from pathlib import Path

import pytest

from portal.models import ExecutionNode
from portal.portfolio_autopilot import command_vector_sha256
from portal import resident_worker_trust as trust


def _seed(tmp_path: Path):
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stand-in reviewed interpreter")
    worker = tmp_path / "local_ollama_worker.py"
    worker.write_bytes(b"stand-in reviewed local worker")
    checkout_index = tmp_path / "checkouts.json"
    checkout_index.write_text('{"schema":"PORTAL_EXISTING_CHECKOUT_INDEX_V1","repositories":{}}')
    cmd = [str(interpreter), str(worker), "--checkout-index", str(checkout_index)]
    manifest = tmp_path / "backends.yaml"
    payload = {"schema":"PORTAL_WORKER_BACKENDS_V1", "workers":[{
        "node_id":"desktop-local", "kind":"PROCESS_JSON_V1",
        "command":cmd, "pass_env":[], "timeout_seconds":90,
    }]}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    nodes = (ExecutionNode(node_id="desktop-local",max_parallel=1),)
    pins = {"expected_command_sha256":command_vector_sha256(cmd),
            "expected_interpreter_sha256":hashlib.sha256(interpreter.read_bytes()).hexdigest(),
            "expected_worker_sha256":hashlib.sha256(worker.read_bytes()).hexdigest()}
    return manifest, nodes, pins, payload, worker
def test_host_accepts_same_exact_worker_as_supervisor(tmp_path):
    manifest,nodes,pins,_,_=_seed(tmp_path)
    result=trust.load_resident_backends(manifest,nodes=nodes,pins=pins,live_auto=True)
    assert len(result)==1
    assert result["desktop-local"].command[2]=="--checkout-index"


def test_host_rejects_missing_pins_before_worker_dispatch(tmp_path):
    manifest,nodes,_,_,_=_seed(tmp_path)
    with pytest.raises(ValueError,match="explicit SHA-256 pins"):
        trust.load_resident_backends(manifest,nodes=nodes,pins={},live_auto=True)


def test_host_rejects_mutated_worker_bytes(tmp_path):
    manifest,nodes,pins,_,worker=_seed(tmp_path)
    worker.write_bytes(b"tampered")
    with pytest.raises(ValueError,match="script digest changed"):
        trust.load_resident_backends(manifest,nodes=nodes,pins=pins,live_auto=True)


def test_host_rejects_manifest_command_swap(tmp_path):
    manifest,nodes,pins,payload,_=_seed(tmp_path)
    other=tmp_path/"alternate.exe"; other.write_bytes(b"not qualified")
    payload["workers"][0]["command"][0]=str(other)
    manifest.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError,match="command digest changed"):
        trust.load_resident_backends(manifest,nodes=nodes,pins=pins,live_auto=True)
def test_host_rejects_manifest_rewrite_during_load(tmp_path, monkeypatch):
    manifest,nodes,pins,payload,_=_seed(tmp_path)
    original=trust.load_worker_backends
    def mutate(path):
        result=original(path)
        payload["workers"][0]["timeout_seconds"]=120
        path.write_text(json.dumps(payload),encoding="utf-8")
        return result
    monkeypatch.setattr(trust,"load_worker_backends",mutate)
    with pytest.raises(ValueError,match="manifest changed"):
        trust.load_resident_backends(manifest,nodes=nodes,pins=pins,live_auto=True)


def test_manual_process_worker_unchanged(tmp_path):
    manifest,nodes,_,_,_=_seed(tmp_path)
    assert trust.load_resident_backends(manifest,nodes=nodes,pins=None,live_auto=False)


def test_host_rejects_credential_inheritance(tmp_path):
    manifest,nodes,pins,payload,_=_seed(tmp_path)
    payload["workers"][0]["pass_env"]=["GITHUB_TOKEN"]
    manifest.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError,match="inherit credentials"):
        trust.load_resident_backends(manifest,nodes=nodes,pins=pins,live_auto=True)


def test_resident_multislot_requires_exact_operator_capacity_pin(tmp_path):
    manifest, _nodes, pins, _payload, _worker = _seed(tmp_path)
    nodes = (ExecutionNode(node_id="desktop-local", max_parallel=13),)
    with pytest.raises(ValueError, match="exact slot count pin"):
        trust.load_resident_backends(
            manifest, nodes=nodes, pins=pins, live_auto=True,
        )
    pins["expected_parallel_slots"] = 12
    with pytest.raises(ValueError, match="exact slot count pin"):
        trust.load_resident_backends(
            manifest, nodes=nodes, pins=pins, live_auto=True,
        )
    pins["expected_parallel_slots"] = 13
    backends = trust.load_resident_backends(
        manifest, nodes=nodes, pins=pins, live_auto=True,
    )
    assert set(backends) == {"desktop-local"}


def test_resident_refuses_slots_over_thirteen_even_with_matching_pin(tmp_path):
    manifest, _nodes, pins, _payload, _worker = _seed(tmp_path)
    pins["expected_parallel_slots"] = 14
    with pytest.raises(ValueError, match="unqualified"):
        trust.load_resident_backends(
            manifest,
            nodes=(ExecutionNode(node_id="desktop-local", max_parallel=14),),
            pins=pins, live_auto=True,
        )
