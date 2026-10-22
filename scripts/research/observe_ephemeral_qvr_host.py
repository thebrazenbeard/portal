"""Research-only external observer of TWO transient real Vera runtime processes.

Requires an isolated source tree, expected Git blob of qualified_runtime.py, and
an explicit scratch parent. No installed route, service, model invocation, trust
provisioning, external transport, or production memory access. Source and process
witnesses are same-host owner-controlled evidence, NOT independent attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
from threading import Thread

SOURCE_SUBPATH = "packages/vera_core/src/vera_core/qualified_runtime.py"
CLAIM_CEILING = "EPHEMERAL_HOST_OBSERVED_PROCESS_AND_SYNTHETIC_COLD_REOPEN"

CHILD_PROGRAM = r'''
import inspect
import json
import os
import sys
from vera_core import QualifiedVeraRuntime, VeraStateDirectory

phase, root, nonce = sys.argv[1:]
state = VeraStateDirectory(root, project_id="scratch-host-witness",
                           identity_id="scratch-vera")
runtime = QualifiedVeraRuntime.from_state_directory(state)
assert runtime.pc_execution_transport is None
assert not runtime.provider_execution_transports
assert not runtime.source_mutation_transports
assert runtime.outbound_trust.generation == 0
assert runtime.audit.context()["effect_count"] == 0

if phase == "initial":
    from vera_memory import AdmissionRequest, MemoryClass
    admitted = runtime.lifecycle.memory.admit(
        AdmissionRequest(
            record_id="scratch-record", text="SYNTHETIC TEST DATA ONLY",
            memory_class=MemoryClass.WORKING_PROJECT,
            source_actor="scratch-host-witness",
            authority_ref="test-ref-not-real-authority",
            privacy_ref="synthetic-only",
            provenance_refs=("scratch:source",),
            operation_id="scratch-operation",
            project_id="scratch-host-witness",
            governed_identity_id="scratch-vera",
        ),
        expected_head=runtime.lifecycle.memory.current_head,
    )
    receipt = runtime.lifecycle.checkpoint(
        checkpoint_id="scratch-checkpoint",
        runtime_id="scratch-process",
        expected_memory_head=admitted["store_head"],
        expected_checkpoint_head=runtime.lifecycle.checkpoints.current_head,
        expected_currentness_generation=None,
        commitments=("scratch-recovery-witness",),
    )
    digest = receipt.recovery_checkpoint_digest
    status = "CHECKPOINT_WRITTEN"
    commitments = ["scratch-recovery-witness"]
elif phase == "reopen":
    recovery = runtime.resume_context()
    digest = recovery.get("accepted_checkpoint_digest")
    status = recovery.get("status")
    commitments = recovery.get("commitments")
else:
    raise RuntimeError("invalid phase")

print(json.dumps({
    "phase": phase, "pid": os.getpid(), "nonce": nonce, "status": status,
    "source_path": inspect.getfile(QualifiedVeraRuntime),
    "checkpoint_digest": digest, "commitments": commitments,
    "effect_count": runtime.audit.context()["effect_count"],
    "trust_scopes": runtime.outbound_trust.context()["scopes"],
}), flush=True)
if sys.stdin.readline().strip() != "RELEASE":
    raise RuntimeError("supervisor did not release child")
'''


def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def read_live_executable(pid: int) -> str:
    """Read an OS process object; not self-reported process identity."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise OSError("unable to open selected process object")
        try:
            capacity = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(capacity.value)
            if not kernel.QueryFullProcessImageNameW(handle, 0, buf,
                                                     ctypes.byref(capacity)):
                raise OSError("unable to inspect process executable")
            return buf.value
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/{pid}/exe")
    raise OSError("unsupported host process witness platform")


def validate_ready(payload: dict, *, phase: str, os_pid: int, nonce: str,
                   exact_source: Path) -> None:
    if (type(payload) is not dict or payload.get("phase") != phase
            or type(payload.get("pid")) is not int
            or payload["pid"] != os_pid
            or payload.get("nonce") != nonce
            or not isinstance(payload.get("source_path"), str)
            or Path(payload["source_path"]).resolve() != exact_source.resolve()
            or type(payload.get("checkpoint_digest")) is not str
            or len(payload["checkpoint_digest"]) != 64
            or payload.get("commitments") != ["scratch-recovery-witness"]
            or payload.get("effect_count") != 0
            or payload.get("trust_scopes") != []):
        raise ValueError("child identity, provenance or safety mismatch")


def _read_line_with_timeout(stream, timeout: float) -> str:
    result = queue.Queue(maxsize=1)
    reader = Thread(target=lambda: result.put(stream.readline()), daemon=True)
    reader.start()
    try:
        line = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("child readiness deadline exceeded") from exc
    if not line or len(line) > 8192:
        raise ValueError("missing or oversized readiness payload")
    return line


def _child(phase: str, source: Path, state: Path, expected_blob: str,
           timeout: float) -> dict:
    import secrets

    nonce = secrets.token_hex(16)
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
            "HOME", "USERPROFILE", "LANG", "LC_ALL"}}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(d) for d in sorted((source / "packages").glob("*/src"))] +
        [str(source)]
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-u", "-c", CHILD_PROGRAM,
         phase, str(state), nonce],
        cwd=str(source), env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None
        line = _read_line_with_timeout(process.stdout, timeout)
        report = json.loads(line)
        validate_ready(report, phase=phase, os_pid=process.pid,
                       nonce=nonce, exact_source=source / SOURCE_SUBPATH)
        if process.poll() is not None:
            raise RuntimeError("child exited before OS observation")
        executable = read_live_executable(process.pid)
        if os.path.normcase(os.path.realpath(executable)) != os.path.normcase(
            os.path.realpath(sys.executable)
        ):
            raise RuntimeError("selected process executable changed")
        if git_blob(source / SOURCE_SUBPATH) != expected_blob:
            raise RuntimeError("source bytes changed during execution")
        assert process.stdin is not None
        out, err = process.communicate(input="RELEASE\n", timeout=timeout)
        if process.returncode != 0:
            raise RuntimeError("child nonzero exit: " + err[-250:])
        if out.strip():
            raise RuntimeError("unexpected child output after release")
        return {"phase": phase, "os_observed_pid": process.pid,
                "os_executable": executable,
                "child_checkpoint_digest": report["checkpoint_digest"],
                "child_status": report["status"]}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def run(source_root: Path, scratch_parent: Path,
        expected_source_git_blob: str, *, timeout: float = 15) -> dict:
    if type(expected_source_git_blob) is not str or len(expected_source_git_blob) != 40:
        raise ValueError("exact source Git blob required")
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("invalid timeout")
    source = Path(source_root)
    parent = Path(scratch_parent)
    if not source.is_dir() or source.is_symlink() or not parent.is_dir():
        raise ValueError("source and scratch parent must preexist as real directories")
    exact = source / SOURCE_SUBPATH
    if not exact.is_file() or exact.is_symlink():
        raise ValueError("missing exact source file")
    if git_blob(exact) != expected_source_git_blob:
        raise ValueError("source Git blob mismatch before any child launch")
    state = Path(tempfile.mkdtemp(prefix="qvr-witness-test-", dir=parent))
    first = _child("initial", source, state, expected_source_git_blob, timeout)
    second = _child("reopen", source, state, expected_source_git_blob, timeout)
    if first["os_observed_pid"] == second["os_observed_pid"]:
        raise RuntimeError("OS reported identical child PIDs")
    if (second["child_checkpoint_digest"] != first["child_checkpoint_digest"]
            or second["child_status"] != "ACCEPTED_CURRENT"):
        raise RuntimeError("cold reopen continuity mismatch")
    uri = (state / "recovery/checkpoints.sqlite").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        count = db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    if count != 1:
        raise RuntimeError("independent database readback mismatch")
    return {
        "claim_ceiling": CLAIM_CEILING, "exact_source_git_blob": expected_source_git_blob,
        "first": first, "second": second, "checkpoint_rows": count,
        "source_installed_verified": False, "live_selected_route_verified": False,
        "persistent_service_verified": False, "external_authority_verified": False,
        "independent_review_verified": False, "inference_executed": False,
        "scratch_state": str(state),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--expected-source-git-blob", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.source_root, args.scratch_parent,
                         args.expected_source_git_blob), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
