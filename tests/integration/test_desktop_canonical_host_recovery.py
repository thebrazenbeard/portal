"""Actual Desktop host processes, pinned sources and disposable roots only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

pytest.importorskip("vera_core", reason="requires Mono #77 source")
pytest.importorskip("pre_active", reason="requires pinned Pre-Active source")

from portal.desktop_host import prepare_runtime_layout
from portal.desktop_ipc import BridgeOutcomeUnknownError, FileBridgeClient
from vera_core import QualifiedVeraRuntime, VeraStateDirectory


def test_actual_host_intake_restart_unknown_claim_and_sqlite_release(tmp_path):
    _check_host_recovery(tmp_path)


def test_denied_response_client_interruption_and_actual_host_cold_recovery(tmp_path):
    _check_host_recovery(tmp_path, interrupt_client=True)


def test_host_error_after_task_open_remains_unknown_across_cold_restart(tmp_path):
    _check_host_recovery(tmp_path, fail_after_open=True)


def _check_host_recovery(tmp_path, *, interrupt_client=False, fail_after_open=False):
    root = tmp_path / "isolated-host"
    layout = prepare_runtime_layout(root)
    (root / "RUNTIME_INSTALL_SPEC.json").write_text(json.dumps({
        "schema": "OFFLINE_TEST_SOURCE_SPEC_ONLY",
        "sources": [{"component": "portal", "sha": "b16413fad12d33a9f9b64d6b1602490f4dede327"}],
    }))
    # A prior durable claim with no response must be reconciled as UNKNOWN.
    original = {"command": "desktop_cognize", "conversation_id": "desktop-cold-test",
                "request_id": "prior-claim", "task": "Original \u03bb input", "source": "HUMAN"}
    claim = layout["claimed"] / "prior-claim.json"
    claim.write_text(json.dumps(original))
    program = '''
import json,os,sys,socket,subprocess,urllib.request
from pathlib import Path
import portal.desktop_host as host
root=Path(sys.argv[1])
# Windows venv python.exe may be a redirector with a different Popen PID.
# This unique, fixture-owned witness identifies the actual interpreter.
with Path(sys.argv[2]).open("x", encoding="utf-8") as witness:
 json.dump({"pid":os.getpid()}, witness)
def forbidden(*args,**kwargs):
 (root/"FORBIDDEN_ROUTE_CALL").write_text("route/model/network boundary was reached")
 raise AssertionError("no route/model/network in host fixture")
socket.create_connection=forbidden
urllib.request.urlopen=forbidden
subprocess.run=forbidden
host.discover_cognition_routes=forbidden
if sys.argv[3]=="1":
 original_handle=host.DesktopRuntimeCommandHandler.handle
 def fail_after_open(self,payload,**kwargs):
  result=original_handle(self,payload,**kwargs)
  if payload.get("request_id")=="new-message":
   raise RuntimeError("injected source failure after TASK_OPENED")
  return result
 host.DesktopRuntimeCommandHandler.handle=fail_after_open
raise SystemExit(host.run_host(root))
'''
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    def start():
        witness = root / f"host-process-{uuid4().hex}.json"
        child = subprocess.Popen([sys.executable, "-B", "-c", program, str(root), str(witness),
                                  str(int(fail_after_open))],
                                 env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(f"host failed: {child.communicate()}")
            heartbeat = layout["bridge_root"] / "heartbeat.json"
            try:
                host_pid = json.loads(witness.read_text(encoding="utf-8"))["pid"]
                current = json.loads(heartbeat.read_text())
                if current["pid"] == host_pid and current["state"] == "ACTIVE":
                    print(f"host ready: launcher_pid={child.pid}, interpreter_pid={host_pid}")
                    return child
            except (OSError, ValueError):
                pass
            time.sleep(0.01)
        child.terminate()
        pytest.fail(f"host did not start: {child.communicate(timeout=5)}")

    def stop(child):
        if child.poll() is None:
            try:
                FileBridgeClient(root, timeout_seconds=5).request("shutdown")
                child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    child.terminate()
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, (stdout, stderr)

    first_process = start()
    client = FileBridgeClient(root, timeout_seconds=5, poll_seconds=0.005)
    try:
        # Poll the same original claim; never publish another request.
        response = layout["responses"] / claim.name
        deadline = time.monotonic() + 5
        while not response.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        result = client.request(**original)
        assert result["status"] == "OUTCOME_UNKNOWN_NO_REDISPATCH"
        if interrupt_client:
            # A separate real client loses response access after dispatch, then
            # exits without normal cleanup. Its original claim must survive.
            client_program = '''
import errno,json,os,sys
from pathlib import Path
from portal.desktop_ipc import FileBridgeClient,BridgeOutcomeUnknownError
root=Path(sys.argv[1])
response=root/"bridge"/"responses"/"new-message.json"
original_read=Path.read_text
def denied(path,*args,**kwargs):
 if path==response:
  raise PermissionError(errno.EACCES,"injected response read denial")
 return original_read(path,*args,**kwargs)
Path.read_text=denied
client=FileBridgeClient(root,timeout_seconds=3,poll_seconds=0.01)
try:
 client.request("desktop_cognize",conversation_id="desktop-cold-test",
                request_id="new-message",task="Exact second message",source="HUMAN")
except BridgeOutcomeUnknownError as exc:
 print(json.dumps({"error":str(exc),"pid":os.getpid()}),flush=True)
 os._exit(23)
raise AssertionError("unreadable response must remain unknown")
'''
            interrupted = subprocess.run([sys.executable, "-B", "-c", client_program, str(root)],
                                         env=env, capture_output=True, text=True, timeout=15)
            assert interrupted.returncode == 23, (interrupted.stdout, interrupted.stderr)
            assert "new-message" in json.loads(interrupted.stdout)["error"]
            print(f"client interrupted after UNKNOWN: {json.loads(interrupted.stdout)['pid']}")
        # Reconcile using exactly the original ID; never another host dispatch.
        if fail_after_open:
            with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
                client.request("desktop_cognize", conversation_id="desktop-cold-test",
                               request_id="new-message", task="Exact second message", source="HUMAN")
            history = client.request("desktop_conversation_history", conversation_id="desktop-cold-test")
            fresh = next(row for row in history["items"] if row["message_id"] == "new-message")
            assert fresh["status"] == "PENDING_NO_ADMITTED_PROVIDER"
        else:
            fresh = client.request("desktop_cognize", conversation_id="desktop-cold-test",
                                   request_id="new-message", task="Exact second message", source="HUMAN")
            assert fresh["status"] == "PENDING_NO_ADMITTED_PROVIDER"
            assert fresh["state"] == "HELD"
    finally:
        stop(first_process)

    second_process = start()
    try:
        history = client.request("desktop_conversation_history", conversation_id="desktop-cold-test")
        by_message = {row["message_id"]: row for row in history["items"]}
        assert set(by_message) == {"prior-claim", "new-message"}
        assert by_message["prior-claim"]["task_id"] == result["task_id"]
        assert by_message["prior-claim"]["source_digest"] == result["source_digest"]
        assert by_message["new-message"]["task_id"] == fresh["task_id"]
        assert all(row["memory_admitted"] is False for row in history["items"])
        assert client.request(**original)["status"] == "OUTCOME_UNKNOWN_NO_REDISPATCH"
        if fail_after_open:
            with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
                client.request("desktop_cognize", conversation_id="desktop-cold-test",
                               request_id="new-message", task="Exact second message", source="HUMAN")
    finally:
        stop(second_process)

    runtime = QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(
        layout["state_root"] / "vera", project_id="VERA_MONO", identity_id="VERA_PROJECT_IDENTITY_V1"))
    assert len(runtime.tasks.events()) == 2
    assert all(not task.closed for task in runtime.tasks.tasks())
    assert runtime.resume_context()["outbound_effect_integrity"]["fence_effect_count"] == 0
    assert not (root / "FORBIDDEN_ROUTE_CALL").exists()
    import sqlite3
    db = sqlite3.connect(layout["state_root"] / "cognition" / "desktop-cognition.sqlite3")
    try:
        assert db.execute("SELECT count(*) FROM desktop_cognition_runs").fetchone()[0] == 0
    finally:
        db.close()
    # Win32 detects leaked SQLite owners; no GC or process-only store remains.
    for path in sorted(layout["state_root"].rglob("*.sqlite*")):
        if not path.is_file():
            continue
        renamed = path.with_name(path.name + ".release-check")
        path.rename(renamed)
        renamed.rename(path)
