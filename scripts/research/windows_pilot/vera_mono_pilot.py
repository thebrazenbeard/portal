"""VeraMonoPilot: manual-start, effectless, noncanonical Windows service.

This is a limited host-runtime pilot, not a cognitive or production service.
No provider transports, PC tools, model execution, network listener, or trust.
"""
from __future__ import annotations
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback

import servicemanager
import win32event
import win32service
import win32serviceutil

ROOT = Path(r"D:\VERA\VeraMonoPilot")
CONFIG = ROOT / "config.json"
RUN = ROOT / "run"
STATE = ROOT / "state"
SOURCE_BLOB = "48e00014b89adeb392102ef45849fbd49fc9dc29"

def blob(path):
    raw = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def installed_fingerprint(root):
    lines = []
    for file in sorted(root.rglob("*")):
        if file.is_symlink():
            raise ValueError("pilot installed tree contains symlink")
        if not file.is_file() or "__pycache__" in file.parts:
            continue
        lines.append(file.relative_to(root).as_posix() + " " +
                     hashlib.sha256(file.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines)

def atomic_json(path, payload):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)

def startup():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = {
        "schema": "VERA_MONO_PILOT_MANUAL_SERVICE_V1",
        "service_name": "VeraMonoPilot",
        "start_mode": "DEMAND",
        "effect_policy": "NO_EXTERNAL_TRANSPORTS",
        "model_provider": "NONE",
        "state_path": str(STATE),
        "install_path": str(ROOT / "installed"),
        "qvr_git_blob": SOURCE_BLOB,
    }
    for key, value in expected.items():
        if cfg.get(key) != value:
            raise RuntimeError("invalid selected pilot configuration: " + key)
    install = ROOT / "installed"
    measured, count = installed_fingerprint(install)
    if measured != cfg.get("installed_tree_sha256"):
        raise RuntimeError("installed artifact changed")
    qvr_file = install / "vera_core" / "qualified_runtime.py"
    if blob(qvr_file) != SOURCE_BLOB:
        raise RuntimeError("selected canonical QVR does not match")
    sys.path.insert(0, str(install))
    from vera_core import QualifiedVeraRuntime, VeraStateDirectory
    if Path(inspect.getfile(QualifiedVeraRuntime)).resolve() != qvr_file.resolve():
        raise RuntimeError("loaded wrong runtime source")
    state = VeraStateDirectory(
        STATE, project_id="vera-mono-pilot", identity_id="vera-pilot"
    )
    runtime = QualifiedVeraRuntime.from_state_directory(state)
    if (runtime.pc_execution_transport is not None
            or runtime.provider_execution_transports
            or runtime.source_mutation_transports
            or runtime.outbound_trust.generation != 0
            or runtime.audit.context()["effect_count"] != 0):
        raise RuntimeError("pilot must remain effectless")
    try:
        recovery = runtime.resume_context()
    except Exception:
        # Do not silently treat an invalid durable state as fresh.
        raise
    if recovery.get("status") == "ACCEPTED_CURRENT":
        digest = recovery["accepted_checkpoint_digest"]
        if recovery.get("commitments") != ["pilot-no-effect-checkpoint"]:
            raise RuntimeError("pilot checkpoint unexpectedly changed")
        mode = "REOPEN"
    elif recovery.get("status") in ("EMPTY", "EMPTY_NO_CHECKPOINT", "NO_CHECKPOINT"):
        from vera_memory import AdmissionRequest, MemoryClass
        admission = runtime.lifecycle.memory.admit(
            AdmissionRequest(
                record_id="pilot-seed-only",
                text="SYNTHETIC PILOT CHECKPOINT. NOT PATRICK PERSONAL MEMORY.",
                memory_class=MemoryClass.WORKING_PROJECT,
                source_actor="vera-mono-pilot",
                authority_ref="pilot-test-only-not-external-authority",
                privacy_ref="synthetic-noncanonical-only",
                provenance_refs=("pilot:bootstrap",),
                operation_id="pilot-setup-seed",
                project_id="vera-mono-pilot",
                governed_identity_id="vera-pilot",
            ),
            expected_head=runtime.lifecycle.memory.current_head,
        )
        checkpoint = runtime.lifecycle.checkpoint(
            checkpoint_id="pilot-cp-1",
            runtime_id="VeraMonoPilot",
            expected_memory_head=admission["store_head"],
            expected_checkpoint_head=runtime.lifecycle.checkpoints.current_head,
            expected_currentness_generation=None,
            commitments=("pilot-no-effect-checkpoint",),
        )
        digest = checkpoint.recovery_checkpoint_digest
        mode = "INITIAL"
    else:
        raise RuntimeError("unexpected pilot recovery status: " + str(recovery.get("status")))
    return {
        "install_digest": measured, "install_files": count,
        "checkpoint_digest": digest, "recovery_mode": mode,
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "source_commit": cfg["source_commit"],
    }, runtime

class VeraMonoPilotService(win32serviceutil.ServiceFramework):
    _svc_name_ = "VeraMonoPilot"
    _svc_display_name_ = "Vera Mono Pilot (Manual, Isolated)"
    _svc_description_ = (
        "Manual-start isolated QualifiedVeraRuntime with synthetic state, "
        "no external transports or model provider."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        try:
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=30000)
            verified, runtime = startup()
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            sequence = 0
            while True:
                sequence += 1
                atomic_json(RUN / "heartbeat.json", {
                    "schema": "VERA_MONO_PILOT_HEARTBEAT_V1",
                    "service": "VeraMonoPilot",
                    "pid": os.getpid(),
                    "sequence": sequence,
                    "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "state": "RUNNING_EFFECTLESS",
                    "model_provider": "NONE",
                    "outbound_effects": 0,
                    **verified,
                })
                if win32event.WaitForSingleObject(self.stop_event, 350) == win32event.WAIT_OBJECT_0:
                    break
            atomic_json(RUN / "last_stop.json", {
                "service": "VeraMonoPilot", "pid": os.getpid(),
                "stop_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "checkpoint_digest": verified["checkpoint_digest"],
            })
        except Exception:
            try:
                atomic_json(RUN / "last_error.json", {
                    "service": "VeraMonoPilot",
                    "error": traceback.format_exc()[-3500:],
                })
            except Exception:
                pass
            raise