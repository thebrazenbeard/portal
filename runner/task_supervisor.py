from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4

from .task_monitor import default_tasks_root, finalize_task, register_task


REQUEST_SCHEMA = "PROJECT_RUNNER_TASK_LAUNCH_REQUEST_V1"


def _windows_current_process_started_at_utc() -> str | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    get_process_times.restype = wintypes.BOOL

    if not get_process_times(
        get_current_process(),
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None

    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    started = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=ticks / 10
    )
    return started.isoformat()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def launch_background_task(
    *,
    name: str,
    command: str,
    owner: str = "chatgpt",
    repository: str | None = None,
    worktree: str | None = None,
    lane: str | None = None,
    work_unit: str | None = None,
    display_command: str | None = None,
    shell: str = "cmd",
    working_directory: Path | None = None,
    tasks_root: Path | None = None,
    registration_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("task-start currently requires Windows")
    if not name.strip():
        raise ValueError("task name must not be empty")
    if not command.strip():
        raise ValueError("task command must not be empty")
    shell = shell.strip().lower()
    if shell not in {"cmd", "powershell"}:
        raise ValueError("task shell must be 'cmd' or 'powershell'")
    if registration_timeout_seconds <= 0:
        raise ValueError("registration timeout must be positive")

    root = Path(tasks_root or default_tasks_root()).expanduser().resolve()
    cwd = Path(working_directory or Path.cwd()).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"task working directory does not exist: {cwd}")

    logs_root = root / "logs"
    requests_root = root / "requests"
    launches_root = root / "launches"
    logs_root.mkdir(parents=True, exist_ok=True)
    requests_root.mkdir(parents=True, exist_ok=True)
    launches_root.mkdir(parents=True, exist_ok=True)

    launch_id = uuid4().hex
    stdout_log = logs_root / f"{launch_id}.stdout.log"
    stderr_log = logs_root / f"{launch_id}.stderr.log"
    supervisor_stdout = logs_root / f"{launch_id}.supervisor.stdout.log"
    supervisor_stderr = logs_root / f"{launch_id}.supervisor.stderr.log"
    request_path = requests_root / f"{launch_id}.json"
    ack_path = launches_root / f"{launch_id}.json"

    _atomic_json_write(
        request_path,
        {
            "schema": REQUEST_SCHEMA,
            "launch_id": launch_id,
            "name": name,
            "command": command,
            "display_command": display_command,
            "shell": shell,
            "repository": repository,
            "worktree": worktree,
            "lane": lane,
            "owner": owner,
            "work_unit": work_unit,
            "working_directory": str(cwd),
            "tasks_root": str(root),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "ack_path": str(ack_path),
        },
    )

    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    try:
        with (
            supervisor_stdout.open("wb") as stdout_handle,
            supervisor_stderr.open("wb") as stderr_handle,
        ):
            supervisor = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "runner.task_supervisor",
                    "--request-path",
                    str(request_path),
                ],
                cwd=cwd,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=creation_flags,
            )
    except BaseException:
        request_path.unlink(missing_ok=True)
        raise

    deadline = time.monotonic() + registration_timeout_seconds
    while time.monotonic() < deadline:
        if ack_path.exists():
            return json.loads(ack_path.read_text(encoding="utf-8"))
        exit_code = supervisor.poll()
        if exit_code is not None:
            detail = (
                supervisor_stderr.read_text(encoding="utf-8", errors="replace")
                if supervisor_stderr.exists()
                else ""
            )
            request_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"task supervisor exited before registration (exit {exit_code}): {detail}"
            )
        time.sleep(0.05)

    supervisor.terminate()
    try:
        supervisor.wait(timeout=2)
    except subprocess.TimeoutExpired:
        supervisor.kill()
        supervisor.wait(timeout=2)
    request_path.unlink(missing_ok=True)
    raise TimeoutError(
        f"task supervisor did not confirm registration within "
        f"{registration_timeout_seconds:g} seconds; PID {supervisor.pid}"
    )


def _windows_command_argv(command: str, shell: str) -> list[str]:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    if shell == "cmd":
        return [
            str(system_root / "System32" / "cmd.exe"),
            "/d",
            "/s",
            "/c",
            command,
        ]
    if shell == "powershell":
        encoded_command = base64.b64encode(
            command.encode("utf-16-le")
        ).decode("ascii")
        return [
            str(
                system_root
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            ),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_command,
        ]
    raise ValueError(f"unsupported task shell: {shell}")


def run_supervisor(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("unsupported task launch request schema")

    tasks_root = Path(str(request["tasks_root"])).resolve()
    working_directory = Path(str(request["working_directory"])).resolve()
    stdout_log = Path(str(request["stdout_log"])).resolve()
    stderr_log = Path(str(request["stderr_log"])).resolve()
    ack_path = Path(str(request["ack_path"])).resolve()
    shell = str(request.get("shell") or "powershell").strip().lower()

    record = register_task(
        tasks_root,
        name=str(request["name"]),
        pid=os.getpid(),
        owner=str(request["owner"]),
        repository=request.get("repository"),
        worktree=request.get("worktree"),
        lane=request.get("lane"),
        work_unit=request.get("work_unit"),
        command=str(request.get("display_command") or request["command"]),
        shell=shell,
        working_directory=str(working_directory),
        process_started_at_utc=_windows_current_process_started_at_utc(),
        stdout_log=str(stdout_log),
        stderr_log=str(stderr_log),
    )
    task_id = str(record["task_id"])

    _atomic_json_write(
        ack_path,
        {
            "mode": "PROJECT_RUNNER_TASK_SUPERVISOR_V2",
            "launch_id": str(request["launch_id"]),
            "task_id": task_id,
            "supervisor_pid": os.getpid(),
            "name": str(request["name"]),
            "shell": shell,
            "tasks_root": str(tasks_root),
        },
    )
    request_path.unlink(missing_ok=True)

    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    command = str(request["command"])
    command_argv = _windows_command_argv(command, shell)

    try:
        with stdout_log.open("wb") as stdout_handle, stderr_log.open("wb") as stderr_handle:
            child = subprocess.Popen(
                command_argv,
                cwd=working_directory,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
            exit_code = int(child.wait())
    except BaseException:
        finalize_task(
            tasks_root,
            task_id=task_id,
            exit_code=125,
            terminal_reason="LAUNCH_FAILED",
        )
        raise

    finalize_task(
        tasks_root,
        task_id=task_id,
        exit_code=exit_code,
        terminal_reason="PROCESS_EXITED",
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-runner-task-supervisor")
    parser.add_argument("--request-path", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_supervisor(args.request_path)


if __name__ == "__main__":
    raise SystemExit(main())
