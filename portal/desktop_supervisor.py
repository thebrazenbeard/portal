from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from enum import Enum
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Iterator


class RuntimeState(str, Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RuntimeSupervisorConfig:
    runtime_root: Path
    heartbeat_ttl_seconds: float = 5.0
    startup_timeout_seconds: float = 30.0

    @property
    def heartbeat_path(self) -> Path:
        return self.runtime_root / "bridge" / "heartbeat.json"

    @property
    def pid_path(self) -> Path:
        return self.runtime_root / "bridge" / "pid.txt"

    @property
    def python_path(self) -> Path:
        return self.runtime_root / ".venv" / "Scripts" / "python.exe"

    @property
    def host_script(self) -> Path:
        return self.runtime_root / "host" / "vera_unified_host.py"


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    reason: str
    runtime_id: str | None = None
    pid: int | None = None
    heartbeat_age_seconds: float | None = None
    components_loaded: bool | None = None


ProcessAlive = Callable[[int], bool]
Launcher = Callable[[Path, Path], int]


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    # HANDLE is pointer-sized; ctypes' default c_int truncates it on Win64.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    synchronize = 0x00100000
    wait_timeout = 258
    handle = kernel.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        # Waiting also distinguishes an exited process whose exit code is 259.
        return kernel.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel.CloseHandle(handle)


def process_is_alive(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


@contextmanager
def _launch_guard(path: Path) -> Iterator[bool]:
    """Serialize launch requests, releasing the OS lock even on process exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            yield False
            return
        try:
            yield True
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _default_launcher(python: Path, script: Path) -> int:
    if not python.is_file():
        raise FileNotFoundError(f"runtime python not found: {python}")
    runtime_root = script.parent.parent.resolve()
    args = [str(python), str(script)] if script.is_file() else [
        str(python), "-m", "portal.desktop_host", "--runtime-root", str(runtime_root)
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    logs = runtime_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, VERA_RUNTIME_ROOT=str(runtime_root))
    with (logs / "runtime.stdout.log").open("ab", buffering=0) as stdout, \
            (logs / "runtime.stderr.log").open("ab", buffering=0) as stderr:
        process = subprocess.Popen(
            args, cwd=str(runtime_root), env=env,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            close_fds=True, creationflags=creationflags,
        )
    return int(process.pid)


class RuntimeSupervisor:
    def __init__(
        self,
        config: RuntimeSupervisorConfig,
        *,
        process_alive: ProcessAlive = process_is_alive,
        launcher: Launcher = _default_launcher,
    ) -> None:
        for value in (config.heartbeat_ttl_seconds, config.startup_timeout_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("supervisor timeouts must be positive and finite")
        self.config = config
        self._process_alive = process_alive
        self._launcher = launcher

    def _pid_hint(self) -> int | None:
        try:
            value = int(
                self.config.pid_path.read_text(
                    encoding="utf-8-sig"
                ).strip()
            )
        except (FileNotFoundError, ValueError, OSError):
            return None
        return value if value > 0 else None

    def _heartbeat_age(
        self,
        payload: dict[str, object],
        *,
        now: float,
    ) -> float:
        observed = payload.get("observed_at")
        if (
            not isinstance(observed, (int, float))
            or isinstance(observed, bool)
            or not math.isfinite(observed)
            or observed > now
        ):
            raise ValueError("heartbeat timestamp must be finite and not future-dated")
        return now - float(observed)

    def _launch_status(self, *, now: float) -> RuntimeStatus | None:
        try:
            payload = json.loads(
                (self.config.runtime_root / "bridge" / "launch.json").read_text(encoding="utf-8")
            )
            pid = payload["pid"]
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("invalid launch pid")
            age = self._heartbeat_age(payload, now=now)
        except FileNotFoundError:
            return None
        except (KeyError, TypeError, ValueError, OSError):
            return RuntimeStatus(RuntimeState.BLOCKED, "launch_reservation_invalid")
        if not self._process_alive(pid):
            return None
        if age > self.config.startup_timeout_seconds:
            return RuntimeStatus(RuntimeState.DEGRADED, "runtime_startup_heartbeat_timeout", pid=pid)
        return RuntimeStatus(RuntimeState.STARTING, "runtime_launch_pending_heartbeat", pid=pid)

    @staticmethod
    def _components_loaded(payload: dict[str, object]) -> bool:
        components = payload.get("components")
        if not isinstance(components, dict):
            return False
        required = ("vera_mono", "portal", "pre_active", "volition")
        for name in required:
            component = components.get(name)
            if not isinstance(component, dict) or component.get("loaded") is not True:
                return False
        return True

    def status(self, *, now: float | None = None) -> RuntimeStatus:
        observed_now = float(time.time() if now is None else now)
        if not math.isfinite(observed_now):
            raise ValueError("now must be finite")
        heartbeat = self.config.heartbeat_path
        if not heartbeat.is_file():
            pid = self._pid_hint()
            if pid is not None and self._process_alive(pid):
                return RuntimeStatus(
                    RuntimeState.DEGRADED,
                    "heartbeat_missing_process_alive",
                    pid=pid,
                )
            return self._launch_status(now=observed_now) or RuntimeStatus(RuntimeState.OFFLINE, "heartbeat_missing")

        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("heartbeat root must be an object")
            pid = payload["pid"]
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("pid must be a positive integer")
            runtime_id = payload["runtime_id"]
            if not isinstance(runtime_id, str) or not runtime_id.strip():
                raise ValueError("runtime_id must be a nonempty string")
            age = self._heartbeat_age(payload, now=observed_now)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return RuntimeStatus(RuntimeState.BLOCKED, "heartbeat_invalid")

        if not self._process_alive(pid):
            pid_hint = self._pid_hint()
            if pid_hint is not None and pid_hint != pid and self._process_alive(pid_hint):
                return RuntimeStatus(RuntimeState.DEGRADED, "heartbeat_pid_mismatch_process_alive", pid=pid_hint)
            return self._launch_status(now=observed_now) or RuntimeStatus(
                RuntimeState.OFFLINE,
                "runtime_process_not_alive",
                runtime_id=runtime_id,
                pid=pid,
                heartbeat_age_seconds=age,
                components_loaded=self._components_loaded(payload),
            )

        loaded = self._components_loaded(payload)
        if age > self.config.heartbeat_ttl_seconds:
            return RuntimeStatus(
                RuntimeState.DEGRADED,
                "heartbeat_stale_process_alive",
                runtime_id=runtime_id,
                pid=pid,
                heartbeat_age_seconds=age,
                components_loaded=loaded,
            )
        reported_state = payload.get("state", RuntimeState.ACTIVE.value)
        if reported_state != RuntimeState.ACTIVE.value or payload.get("failure"):
            try:
                state = RuntimeState(reported_state)
            except ValueError:
                state = RuntimeState.BLOCKED
            if state in (RuntimeState.ACTIVE, RuntimeState.OFFLINE):
                state = RuntimeState.DEGRADED
            return RuntimeStatus(
                state,
                str(payload.get("failure") or payload.get("reason") or "runtime_reported_unhealthy"),
                runtime_id=runtime_id,
                pid=pid,
                heartbeat_age_seconds=age,
                components_loaded=loaded,
            )
        if not loaded:
            return RuntimeStatus(
                RuntimeState.DEGRADED,
                "component_not_loaded",
                runtime_id=runtime_id,
                pid=pid,
                heartbeat_age_seconds=age,
                components_loaded=False,
            )
        return RuntimeStatus(
            RuntimeState.ACTIVE,
            "runtime_healthy",
            runtime_id=runtime_id,
            pid=pid,
            heartbeat_age_seconds=age,
            components_loaded=True,
        )

    def ensure_started(self, *, now: float | None = None) -> RuntimeStatus:
        bridge = self.config.runtime_root / "bridge"
        with _launch_guard(bridge / "launch.lock") as acquired:
            if not acquired:
                return RuntimeStatus(RuntimeState.STARTING, "runtime_launch_in_progress")
            current = self.status(now=now)
            if current.state is not RuntimeState.OFFLINE:
                return current
            try:
                pid = self._launcher(self.config.python_path, self.config.host_script)
            except OSError as exc:
                return RuntimeStatus(RuntimeState.BLOCKED, f"runtime_launch_failed: {exc}")
            # Retain the reservation after releasing the guard, until the host
            # publishes its heartbeat. Other desktop processes share this file.
            pending = bridge / "launch.json.tmp"
            pending.write_text(
                json.dumps({"pid": pid, "observed_at": time.time() if now is None else now}),
                encoding="utf-8",
            )
            pending.replace(bridge / "launch.json")
            return RuntimeStatus(RuntimeState.STARTING, "runtime_launch_requested", pid=pid)


def main(argv: list[str] | None = None) -> int:
    """Thin logon entry point; the OS owns process activation and lifetime."""
    import argparse
    from dataclasses import asdict
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=os.environ.get("VERA_RUNTIME_ROOT"))
    args = parser.parse_args(argv)
    if args.runtime_root is None:
        parser.error("--runtime-root or VERA_RUNTIME_ROOT is required")
    status = RuntimeSupervisor(RuntimeSupervisorConfig(args.runtime_root)).ensure_started()
    print(json.dumps(asdict(status), sort_keys=True))
    return 1 if status.state in (RuntimeState.BLOCKED, RuntimeState.DEGRADED) and status.components_loaded is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
