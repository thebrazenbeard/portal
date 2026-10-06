from __future__ import annotations

from datetime import datetime, timedelta, timezone
import errno
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4


TASK_SCHEMA = "PROJECT_RUNNER_LOCAL_TASK_V1"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "UNKNOWN_EXIT"}


def default_tasks_root() -> Path:
    override = os.environ.get("PROJECT_RUNNER_TASKS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "ProjectRunner" / "tasks"

    return Path.home() / ".project-runner" / "tasks"


def register_task(
    tasks_root: Path,
    *,
    name: str,
    pid: int,
    owner: str,
    repository: str | None = None,
    worktree: str | None = None,
    lane: str | None = None,
    work_unit: str | None = None,
    command: str | None = None,
    shell: str | None = None,
    working_directory: str | None = None,
    process_started_at_utc: str | None = None,
    stdout_log: str | None = None,
    stderr_log: str | None = None,
) -> dict[str, Any]:
    if pid <= 0:
        raise ValueError("task pid must be positive")
    if not name.strip():
        raise ValueError("task name must not be empty")
    if not owner.strip():
        raise ValueError("task owner must not be empty")

    active = Path(tasks_root) / "active"
    active.mkdir(parents=True, exist_ok=True)
    task_id = uuid4().hex
    record: dict[str, Any] = {
        "schema": TASK_SCHEMA,
        "task_id": task_id,
        "name": name,
        "pid": pid,
        "owner": owner,
        "repository": repository,
        "worktree": worktree,
        "lane": lane,
        "work_unit": work_unit,
        "command": command,
        "shell": shell,
        "working_directory": working_directory,
        "process_started_at_utc": process_started_at_utc,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "REGISTERED",
    }
    _atomic_json_write(active / f"{task_id}.json", record)
    return record


def finalize_task(
    tasks_root: Path,
    *,
    task_id: str,
    exit_code: int,
    terminal_reason: str = "PROCESS_EXITED",
) -> dict[str, Any]:
    return _archive_terminal_task(
        tasks_root,
        task_id=task_id,
        state="COMPLETED" if exit_code == 0 else "FAILED",
        exit_code=exit_code,
        terminal_reason=terminal_reason,
    )


def reconcile_orphaned_tasks(tasks_root: Path) -> list[dict[str, Any]]:
    active = Path(tasks_root) / "active"
    reconciled: list[dict[str, Any]] = []
    if not active.exists():
        return reconciled

    for path in sorted(active.glob("*.json")):
        record = _load_task_record(path)
        if record is None:
            continue
        runtime_state = _process_runtime_state(record)
        if runtime_state in {"RUNNING", "IDENTITY_UNVERIFIED"}:
            continue
        reason = (
            "PROCESS_IDENTITY_REUSED_WITHOUT_FINAL_RECEIPT"
            if runtime_state == "PID_REUSED"
            else "PROCESS_GONE_WITHOUT_FINAL_RECEIPT"
        )
        reconciled.append(
            _archive_terminal_task(
                tasks_root,
                task_id=str(record["task_id"]),
                state="UNKNOWN_EXIT",
                exit_code=None,
                terminal_reason=reason,
            )
        )
    return reconciled


def summarize_tasks(
    tasks_root: Path,
    *,
    lane: str | None = None,
) -> dict[str, Any]:
    active = Path(tasks_root) / "active"
    tasks: list[dict[str, Any]] = []
    lane_counts: dict[str, dict[str, int]] = {}
    if active.exists():
        for path in sorted(active.glob("*.json")):
            record = _load_task_record(path)
            if record is None:
                continue
            if lane is not None and record.get("lane") != lane:
                continue
            task = dict(record)
            task["state"] = _process_runtime_state(task)
            tasks.append(task)
            lane_id = str(task.get("lane") or "unassigned")
            counts = lane_counts.setdefault(lane_id, {})
            state = str(task["state"])
            counts[state] = counts.get(state, 0) + 1
    return {
        "mode": "PROJECT_RUNNER_TASK_MONITOR_V1",
        "lane_filter": lane,
        "lane_counts": lane_counts,
        "tasks": tasks,
    }


def summarize_task_history(
    tasks_root: Path,
    *,
    lane: str | None = None,
) -> dict[str, Any]:
    history = Path(tasks_root) / "history"
    tasks: list[dict[str, Any]] = []
    if history.exists():
        for path in sorted(history.glob("*.json")):
            record = _load_task_record(path)
            if record is None:
                continue
            if lane is not None and record.get("lane") != lane:
                continue
            tasks.append(record)
    tasks.sort(key=lambda item: str(item.get("ended_at_utc") or ""), reverse=True)
    return {
        "mode": "PROJECT_RUNNER_TASK_HISTORY_V1",
        "lane_filter": lane,
        "tasks": tasks,
    }


def _archive_terminal_task(
    tasks_root: Path,
    *,
    task_id: str,
    state: str,
    exit_code: int | None,
    terminal_reason: str,
) -> dict[str, Any]:
    if state not in TERMINAL_STATES:
        raise ValueError(f"invalid terminal task state: {state}")
    if re.fullmatch(r"[0-9a-f]{32}", task_id) is None:
        raise ValueError("invalid task id")

    root = Path(tasks_root)
    active_path = root / "active" / f"{task_id}.json"
    history = root / "history"
    history_path = history / f"{task_id}.json"

    if not active_path.exists():
        if history_path.exists():
            record = _load_task_record(history_path)
            if record is None:
                raise ValueError(f"invalid historical task record: {task_id}")
            return record
        raise ValueError(f"active task not found: {task_id}")

    record = _load_task_record(active_path)
    if record is None:
        raise ValueError(f"invalid active task record: {task_id}")
    if str(record.get("task_id")) != task_id:
        raise ValueError("task id does not match active record")

    ended = datetime.now(timezone.utc)
    record["state"] = state
    record["status"] = state
    record["exit_code"] = exit_code
    record["terminal_reason"] = terminal_reason
    record["ended_at_utc"] = ended.isoformat()

    started_raw = record.get("started_at_utc")
    if isinstance(started_raw, str):
        try:
            started = datetime.fromisoformat(started_raw)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            record["duration_seconds"] = max(
                0.0, (ended - started.astimezone(timezone.utc)).total_seconds()
            )
        except ValueError:
            record["duration_seconds"] = None
    else:
        record["duration_seconds"] = None

    history.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(active_path, record)
    os.replace(active_path, history_path)
    return record


def _load_task_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("schema") != TASK_SCHEMA:
        return None
    return record


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


def _process_matches_record(record: dict[str, Any]) -> bool:
    return _process_runtime_state(record) == "RUNNING"


def _process_runtime_state(record: dict[str, Any]) -> str:
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return "IDENTITY_UNVERIFIED"
    running = _process_is_running(pid)
    if running is None:
        return "IDENTITY_UNVERIFIED"
    if not running:
        return "ORPHANED"

    expected = record.get("process_started_at_utc")
    if expected is None:
        return "RUNNING"
    if not isinstance(expected, str) or not expected.strip():
        return "IDENTITY_UNVERIFIED"

    observed = _process_started_at_utc(pid)
    if observed is None:
        return "IDENTITY_UNVERIFIED"

    match = _process_start_times_match(expected, observed)
    if match is None:
        return "IDENTITY_UNVERIFIED"
    return "RUNNING" if match else "PID_REUSED"


def _process_start_times_match(expected: str, observed: str) -> bool | None:
    try:
        expected_dt = datetime.fromisoformat(expected)
        observed_dt = datetime.fromisoformat(observed)
    except ValueError:
        return None
    if expected_dt.tzinfo is None or observed_dt.tzinfo is None:
        return None
    delta = abs(
        (
            expected_dt.astimezone(timezone.utc)
            - observed_dt.astimezone(timezone.utc)
        ).total_seconds()
    )
    return delta <= 1.0


def _process_started_at_utc(pid: int) -> str | None:
    if pid <= 0 or os.name != "nt":
        return None
    return _windows_process_started_at_utc(pid)


def _process_is_running(pid: int) -> bool | None:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _windows_process_is_running(pid: int) -> bool | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        # An absent PID reports ERROR_INVALID_PARAMETER. Access denial or
        # another query failure cannot prove that the process has terminated.
        if ctypes.get_last_error() == error_invalid_parameter:
            return False
        return None
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def _windows_process_started_at_utc(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not get_process_times(
            handle,
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
    finally:
        close_handle(handle)
