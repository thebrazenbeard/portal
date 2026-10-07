"""Resident qualification with the bound component APIs and a fake local model."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("pre_active")
pytest.importorskip("vera_core")
pytest.importorskip("volition")

from portal.desktop_cognition import CognitionRoute
from portal.desktop_host import ResidentHost, RuntimeSingleton, load_manifest, run_host, serve
from portal.desktop_ipc import FileBridgeClient


LOCAL = CognitionRoute("ollama:fake", "ollama", "fake", True, True, True,
                       ("text",), False, True, "COGNITION_ONLY_NO_PROTECTED_EFFECT")
MANIFEST = {"schema": "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1", "install_id": "test",
            "identity": {"project_id": "portal-test", "identity_id": "vera"},
            "cognition_policy": {"allow_local_no_paid_compute": True}, "sources": []}


def test_real_engine_autonomy_has_qualified_receipt_and_does_not_replay(tmp_path):
    with ResidentHost(tmp_path, MANIFEST, discover=lambda: (LOCAL,),
                      invoke=lambda route, task: "bounded observation") as host:
        event_id = host.pre_active.request_autonomous_turn(
            task="inspect local status", capabilities=set(), source="TEMPORAL",
            reason="qualification", now=time.time())
        for _ in range(4):
            host.tick()
        event = next(e for e in host.pre_active.list_events() if e["id"] == event_id)
        assert event["status"] == "DONE"
        row = host.ledger.list_recent()[0]
        assert row["state"] == "COMPLETED"
        assert row["source"] == "PRE_ACTIVE_AUTONOMOUS_TURN"
        assert row["reason"] == "qualification"
        assert row["provider"] == "ollama"
        receipt = json.loads(row["acceptance_json"])
        assert receipt["status"] == "ACCEPTED_HOST_OBSERVATION"
        assert receipt["canonical_memory_write"] is False
        assert receipt["acceptance_digest"]
        assert row["protected_effect_authority"] == 0
        assert host.status()["state"] == "ACTIVE"
        assert host.daemon.engine.tools.specs(set()) == []
    with ResidentHost(tmp_path, MANIFEST, discover=lambda: (LOCAL,),
                      invoke=lambda *args: pytest.fail("completed cognition must not replay")) as restarted:
        restarted.tick()
        assert restarted.ledger.list_recent()[0]["evidence_id"] == row["evidence_id"]


def test_no_route_preserves_autonomous_event_until_route_returns(tmp_path):
    routes = []
    with ResidentHost(tmp_path, MANIFEST, discover=lambda: tuple(routes),
                      invoke=lambda *args: "recovered") as host:
        event_id = host.pre_active.request_autonomous_turn(
            task="retry later", capabilities=set(), source="OPEN_LOOP", reason="test", now=time.time())
        for _ in range(8):
            host.tick()
        assert host.status()["state"] == "BLOCKED"
        event = next(e for e in host.pre_active.list_events() if e["id"] == event_id)
        assert event["status"] == "PENDING"
        assert event["attempts"] == 0
        routes.append(LOCAL)
        for _ in range(3):
            host.tick()
        assert host.ledger.list_recent()[0]["state"] == "COMPLETED"


def test_volition_remains_resident_when_no_route_and_runs_after_admission(tmp_path):
    routes = []
    with ResidentHost(tmp_path, MANIFEST, discover=lambda: tuple(routes), invoke=lambda *args: "motive result") as host:
        signal_id = host.volition.enqueue_signal(payload={
            "target": "understand local state", "source": "qualification",
            "kind": "epistemic", "magnitude": 0.9, "confidence": 1,
            "expected_information_gain": 1, "provenance": "current_observation",
        }, now=time.time())
        host.tick()
        receipt = host.pre_active.get_volition_signal_receipt(signal_id)
        assert receipt["cognition_event_id"]
        assert host.pre_active.get_volition_state() is not None
        assert next(e for e in host.pre_active.list_events(kind="autonomous.turn") if e["id"] == receipt["cognition_event_id"])["status"] == "PENDING"
        routes.append(LOCAL)
        for _ in range(5):
            host.tick()
        assert host.ledger.list_recent()[0]["state"] == "COMPLETED"


def test_singleton_owns_root_independently_of_source_revision(tmp_path):
    with RuntimeSingleton(tmp_path):
        with pytest.raises(RuntimeError, match="already"):
            with RuntimeSingleton(tmp_path):
                pass
        with RuntimeSingleton(tmp_path / "other-root"):
            pass
    with RuntimeSingleton(tmp_path):
        pass


def _wait_for(path: Path, predicate=lambda body: True, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            body = json.loads(path.read_text())
            if predicate(body):
                return body
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def test_slow_model_keeps_heartbeat_and_status_responsive_and_responses_durable(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    stop = threading.Event()
    calls = []

    def invoke(route, task):
        calls.append(task)
        entered.set()
        assert release.wait(5)
        return "slow cognition result"

    def factory(root, manifest):
        host = ResidentHost(root, manifest, discover=lambda: (LOCAL,), invoke=invoke)
        host.portal._ensure_session(session_id="known-session", holder="qualification", now=time.time())
        return host
    server = threading.Thread(target=serve, args=(tmp_path, MANIFEST), kwargs={"stop": stop, "host_factory": factory})
    server.start()
    try:
        _wait_for(tmp_path / "bridge/heartbeat.json", lambda b: b["state"] == "ACTIVE")
        cognition = tmp_path / "bridge/requests/slow.json"
        cognition.write_text(json.dumps({"command": "desktop_cognize", "request_id": "slow", "reason": "test", "task": "slow task"}))
        assert entered.wait(3)
        client = FileBridgeClient(tmp_path, timeout_seconds=1)
        status = client.request("desktop_status")
        assert status["pid"] > 0
        assert status["state"] == "ACTIVE"
        assert status["worker_operation"] == "desktop_cognize"
        began = time.monotonic()
        portfolio = FileBridgeClient(tmp_path, timeout_seconds=.5, poll_seconds=.01).request(
            "desktop_portfolio", action="status", session_id="new-session")
        assert time.monotonic() - began < .5
        assert portfolio["configured"] is False
        assert portfolio["session"] is None
        assert portfolio["cache"] is True
        assert portfolio["observed_at"] <= time.time()
        cached_session = FileBridgeClient(tmp_path, timeout_seconds=.5, poll_seconds=.01).request(
            "desktop_portfolio", action="status", session_id="known-session")
        assert cached_session["session"]["control_state"] == "RUNNING"
        assert "known-session" in cached_session["sessions"]
        mutation_result = {}
        def stop_portfolio():
            mutation_result.update(FileBridgeClient(tmp_path, timeout_seconds=3).request(
                "desktop_portfolio", action="stop", session_id="known-session"))
        mutation = threading.Thread(target=stop_portfolio)
        mutation.start()
        time.sleep(.2)
        assert mutation.is_alive()
        still_running = FileBridgeClient(tmp_path, timeout_seconds=.5, poll_seconds=.01).request(
            "desktop_portfolio", action="status", session_id="known-session")
        assert still_running["session"]["control_state"] == "RUNNING"
        first_heartbeat = _wait_for(tmp_path / "bridge/heartbeat.json")["observed_at"]
        _wait_for(tmp_path / "bridge/heartbeat.json", lambda b: b["observed_at"] > first_heartbeat)
        release.set()
        mutation.join(3)
        assert not mutation.is_alive()
        assert mutation_result["session"]["control_state"] == "STOPPED"
        response = _wait_for(tmp_path / "bridge/responses/slow.json")
        assert response["result"]["state"] == "COMPLETED"
        assert response["result"]["provenance"]["provider"] == "ollama"
        assert response["result"]["provenance"]["acceptance"]["status"] == "ACCEPTED_HOST_OBSERVATION"
        cognition.write_text(json.dumps({"command": "desktop_cognize", "request_id": "slow", "reason": "test", "task": "slow task"}))
        time.sleep(0.4)
        assert calls == ["slow task"]
        assert (tmp_path / "bridge/responses/slow.json").is_file()
        mismatch = tmp_path / "bridge/requests/filename.json"
        mismatch.write_text(json.dumps({"command": "desktop_cognize", "request_id": "different", "reason": "test", "task": "bad"}))
        rejected = _wait_for(tmp_path / "bridge/responses/filename.json")
        assert rejected["ok"] is False
        assert "filename" in rejected["error"]
        assert calls == ["slow task"]
    finally:
        release.set()
        stop.set()
        server.join(6)
    assert not server.is_alive()


def test_worker_failure_is_truthful_and_status_ipc_still_works(tmp_path):
    stop = threading.Event()
    def broken(root, manifest):
        raise RuntimeError("component construction failed")
    server = threading.Thread(target=serve, args=(tmp_path, MANIFEST), kwargs={"stop": stop, "host_factory": broken})
    server.start()
    try:
        heartbeat = _wait_for(tmp_path / "bridge/heartbeat.json", lambda b: b["state"] == "BLOCKED")
        assert "component construction failed" in heartbeat["failure"]
        assert FileBridgeClient(tmp_path, timeout_seconds=1).request("desktop_status")["state"] == "BLOCKED"
    finally:
        stop.set()
        server.join(3)
    assert not server.is_alive()


def test_stalled_cognition_marks_degraded_without_losing_heartbeat(tmp_path):
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    def invoke(route, task):
        entered.set()
        release.wait(5)
        return "returned"
    factory = lambda root, manifest: ResidentHost(root, manifest, discover=lambda: (LOCAL,), invoke=invoke)
    server = threading.Thread(target=serve, args=(tmp_path, MANIFEST), kwargs={
        "stop": stop, "host_factory": factory, "worker_timeout_seconds": .15})
    server.start()
    try:
        _wait_for(tmp_path / "bridge/heartbeat.json", lambda b: b["state"] == "ACTIVE")
        (tmp_path / "bridge/requests/stalled.json").write_text(json.dumps({
            "command": "desktop_cognize", "request_id": "stalled", "reason": "test", "task": "wait"}))
        assert entered.wait(3)
        degraded = _wait_for(tmp_path / "bridge/heartbeat.json", lambda b:
                             b["state"] == "DEGRADED" and b.get("worker_operation") == "desktop_cognize")
        assert degraded["failure"] == "resident_worker_not_progressing"
        assert degraded["worker_alive"] is True
        status = FileBridgeClient(tmp_path, timeout_seconds=1).request("desktop_status")
        assert status["state"] == "DEGRADED"
        release.set()
        _wait_for(tmp_path / "bridge/heartbeat.json", lambda b: b["state"] == "ACTIVE")
    finally:
        release.set()
        stop.set()
        server.join(6)
    assert not server.is_alive()


def _source_spec(root):
    sources = [{"component": component, "repository": "thebrazenbeard/" + component,
                "ref": "main", "sha": "a" * 40}
               for component in ("vera-mono", "portal", "pre-active", "volition")]
    spec = {**MANIFEST, "sources": sources}
    (root / "RUNTIME_INSTALL_SPEC.json").write_text(json.dumps(spec))
    return spec


def test_manifest_checks_exact_heads_tracked_cleanliness_and_import_origin(tmp_path, monkeypatch):
    spec = _source_spec(tmp_path)
    dirty = {"value": ""}
    head = {"value": "a" * 40}
    origin = {"outside": False}
    def git_output(args, **kwargs):
        return head["value"] if "rev-parse" in args else dirty["value"]
    names = {"vera_core": "vera-mono", "portal": "portal", "pre_active": "pre-active", "volition": "volition"}
    def imported(name):
        path = tmp_path / "sources" / names[name] / name / "__init__.py"
        if origin["outside"]:
            path = tmp_path / "outside" / "__init__.py"
        return SimpleNamespace(__file__=str(path))
    monkeypatch.setattr("portal.desktop_host.subprocess.check_output", git_output)
    monkeypatch.setattr("portal.desktop_host.importlib.import_module", imported)
    assert load_manifest(tmp_path) == spec
    head["value"] = "b" * 40
    with pytest.raises(ValueError, match="source drift"):
        load_manifest(tmp_path)
    head["value"] = "a" * 40
    dirty["value"] = " M tracked.py"
    with pytest.raises(ValueError, match="source drift"):
        load_manifest(tmp_path)
    dirty["value"] = ""
    origin["outside"] = True
    with pytest.raises(ValueError, match="outside bound source"):
        load_manifest(tmp_path)


def test_check_instantiates_disabled_policy_runtime_under_singleton(tmp_path, monkeypatch, capsys):
    manifest = {**MANIFEST, "cognition_policy": {"allow_local_no_paid_compute": False}}
    observed_policies = []
    def discover(**kwargs):
        observed_policies.append(kwargs["allow_local_no_paid_compute"])
        return ()
    monkeypatch.setattr("portal.desktop_host.load_manifest", lambda root: manifest)
    monkeypatch.setattr("portal.desktop_host.discover_cognition_routes", discover)
    assert run_host(tmp_path, check=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["components_loaded"] is True
    assert result["state"] == "BLOCKED"
    assert observed_policies == [False]
    assert (tmp_path / "state/pre-active/runtime.sqlite3").is_file()
    with RuntimeSingleton(tmp_path):
        assert run_host(tmp_path, check=True) == 1
    assert "already owns" in (tmp_path / "host-error.log").read_text()
