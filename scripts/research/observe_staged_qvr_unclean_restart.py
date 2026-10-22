"""Bounded, lab-only, host-observed unclean restart of an installed QVR.

Research acceptance harness, not a Windows service or deployment mechanism.
It extracts the exact child program from the pinned #76 lab gate; it never
registers a service, grants trust, invokes an inference model, or writes
canonical Vera state. Operator-controlled same-host evidence is NOT attestation.
"""
from __future__ import annotations
import argparse
import ast
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

SOURCE_RELATIVE = Path("vera_core/qualified_runtime.py")
EXPECTED_TARGET = "vera-mono-0.1.0-QVR"
CLAIM_CEILING = "LAB_INSTALLED_SELECTED_UNCLEAN_RESTART_OBSERVED"


def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def installed_tree_digest(root: Path) -> str:
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in installed artifact tree")
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        lines.append(path.relative_to(root).as_posix() + " " +
                     hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def extract_child_program(gate_source: Path) -> str:
    tree = ast.parse(gate_source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CHILD"
            for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and type(node.value.value) is str:
                return node.value.value
    raise ValueError("pinned gate missing literal child program")


def preflight(*, gate: Path, install: Path, route: Path, scratch: Path,
              gate_blob: str, qvr_blob: str) -> tuple[dict, str]:
    if not all(
        type(value) is str and len(value) == 40 and
        all(c in "0123456789abcdef" for c in value)
        for value in (gate_blob, qvr_blob)
    ):
        raise ValueError("pinned Git source blobs must be SHA1 hex")
    for directory in (install, scratch):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("missing or symlinked directory")
    if ".scratch" not in scratch.resolve().parts:
        raise ValueError("scratch parent must be underneath a .scratch directory")
    for file in (gate, route, install / SOURCE_RELATIVE):
        if file.is_symlink() or not file.is_file():
            raise ValueError("missing or symlinked source/route file")
    if git_blob(gate) != gate_blob:
        raise ValueError("pinned gate source differs")
    if git_blob(install / SOURCE_RELATIVE) != qvr_blob:
        raise ValueError("installed QVR source differs")
    selection = json.loads(route.read_text(encoding="utf-8"))
    if type(selection) is not dict:
        raise ValueError("route must be mapping")
    if (selection.get("schema") != "SCRATCH_QVR_SELECTED_ROUTE_V1"
            or selection.get("selected") is not True
            or selection.get("mode") != "LAB_ONLY"
            or selection.get("target") != EXPECTED_TARGET
            or selection.get("source_git_blob") != qvr_blob
            or selection.get("install_root") != str(install)):
        raise ValueError("not an exact selected laboratory route")
    digest = installed_tree_digest(install)
    if selection.get("installed_files_sha256") != digest:
        raise ValueError("installed tree diverges from route")
    return selection, extract_child_program(gate)


def os_executable(pid: int) -> str:
    if sys.platform == "win32":
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
            raise OSError("selected PID not found")
        try:
            size = wintypes.DWORD(4096)
            buff = ctypes.create_unicode_buffer(4096)
            if not kernel.QueryFullProcessImageNameW(handle, 0, buff,
                                                     ctypes.byref(size)):
                raise OSError("unable to resolve selected process image")
            return buff.value
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/{pid}/exe")
    raise OSError("host process observation unsupported")


def observe(p: subprocess.Popen, heartbeat: Path, *, timeout: float) -> tuple[dict, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if p.poll() is not None:
            raise RuntimeError("QVR child exited before observation")
        if heartbeat.exists():
            try:
                observation = json.loads(heartbeat.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                observation = None
            if type(observation) is dict and observation.get("pid") == p.pid and type(
                observation.get("sequence")
            ) is int and observation["sequence"] >= 2:
                executable = os_executable(p.pid)
                if os.path.normcase(os.path.realpath(executable)) != os.path.normcase(
                    os.path.realpath(sys.executable)
                ):
                    raise RuntimeError("wrong OS process executable")
                if observation.get("effect_count") != 0 or observation.get("provider_count") != 0:
                    raise RuntimeError("unexpected outward-capable runtime")
                return observation, executable
        time.sleep(0.06)
    raise TimeoutError("no observed live heartbeat")


def run(*, gate: Path, install: Path, route_template: Path, scratch: Path,
        gate_blob: str, qvr_blob: str, timeout: float = 8) -> dict:
    if type(timeout) not in (int, float) or not 0 < timeout <= 12:
        raise ValueError("bounded timeout required")
    selection, program = preflight(
        gate=gate, install=install, route=route_template, scratch=scratch,
        gate_blob=gate_blob, qvr_blob=qvr_blob,
    )
    test_root = Path(tempfile.mkdtemp(prefix="qvr-unclean-gate-", dir=scratch))
    selection = dict(selection, state_root=str(test_root / "state"))
    route = test_root / "route.json"
    with route.open("x", encoding="utf-8") as fp:
        json.dump(selection, fp, sort_keys=True)
        fp.flush()
        os.fsync(fp.fileno())
    route_digest = hashlib.sha256(route.read_bytes()).hexdigest()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"

    def start(phase: str):
        return subprocess.Popen(
            [sys.executable, "-B", "-u", "-c", program, str(route), phase],
            cwd=str(test_root), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    first = start("first")
    try:
        before, first_exe = observe(first, test_root / "first.heartbeat.json",
                                    timeout=timeout)
        if (before.get("route_digest") != route_digest
                or before.get("loaded_qvr") != str(install / SOURCE_RELATIVE)):
            raise RuntimeError("wrong first route/installed consumer")
        checkpoint = before.get("checkpoint_digest")
        if type(checkpoint) is not str or len(checkpoint) != 64:
            raise RuntimeError("missing first checkpoint")
        first.kill()  # Only the process just created by this test.
        first.communicate(timeout=5)
        if first.returncode == 0 or (test_root / "first.stop").exists():
            raise RuntimeError("injected unclean termination was not observed")
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=5)

    second = start("second")
    try:
        after, second_exe = observe(second, test_root / "second.heartbeat.json",
                                   timeout=timeout)
        if (second.pid == first.pid or after.get("state_status") != "ACCEPTED_CURRENT"
                or after.get("checkpoint_digest") != checkpoint
                or after.get("route_digest") != route_digest
                or after.get("loaded_qvr") != str(install / SOURCE_RELATIVE)):
            raise RuntimeError("unclean restart recovery failed")
        (test_root / "second.stop").write_text("STOP", encoding="ascii")
        output, err = second.communicate(timeout=5)
        if second.returncode != 0 or "STOP_ACK" not in output:
            raise RuntimeError("restart did not shut down cleanly: " + err[-350:])
    finally:
        if second.poll() is None:
            second.kill()
            second.communicate(timeout=5)

    if installed_tree_digest(install) != selection["installed_files_sha256"]:
        raise RuntimeError("installed artifact mutated during test")
    with sqlite3.connect(
        (test_root / "state/recovery/checkpoints.sqlite").as_uri() + "?mode=ro",
        uri=True,
    ) as db:
        count = db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    if count != 1:
        raise RuntimeError("checkpoint readback mismatch")
    return {
        "claim_ceiling": CLAIM_CEILING,
        "first_os_pid": first.pid,
        "first_exit": first.returncode,
        "second_os_pid": second.pid,
        "second_exit": second.returncode,
        "first_executable": first_exe,
        "second_executable": second_exe,
        "checkpoint_digest": checkpoint,
        "recovery_status": after["state_status"],
        "sqlite_checkpoint_rows": count,
        "route_digest": route_digest,
        "installed_tree_digest": selection["installed_files_sha256"],
        "service_registered": False,
        "production_route_selected": False,
        "external_inference_executed": False,
        "independent_review": False,
        "scratch_root": str(test_root),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--route-template", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--gate-blob", required=True)
    parser.add_argument("--qvr-blob", required=True)
    opts = parser.parse_args(argv)
    print(json.dumps(run(
        gate=opts.gate, install=opts.install, route_template=opts.route_template,
        scratch=opts.scratch, gate_blob=opts.gate_blob, qvr_blob=opts.qvr_blob,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
