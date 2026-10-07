from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Callable


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

    @property
    def heartbeat_path(self) -> Path:
        return self.runtime_root / "bridge" / "heartbeat.json"

    @property
    def pid_path(self) -> Path:
        return self.runtime_root / "bridge" / "pid.txt"

    @property
    def python_path(self) -> Path:
        return self.runtime_root / ".venv" / "Scripts" / "python.exe"

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

    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(
            handle,
            ctypes.byref(exit_code),
        ):
            return False
        return int(exit_code.value) == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def discover_active_runtime_root(base_dir: Path) -> Path | None:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return None

    candidates: list[tuple[float, Path]] = []
    for runtime_root in base_dir.iterdir():
        if not runtime_root.is_dir():
            continue
        spec_path = runtime_root / "RUNTIME_INSTALL_SPEC.json"
        qualification_path = runtime_root / "QUALIFICATION.json"
        if not spec_path.is_file() or not qualification_path.is_file():
            continue
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
            qualification = json.loads(
                qualification_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(spec, dict) or not isinstance(qualification, dict):
            continue
        activation = spec.get("activation")
        if (
            not isinstance(activation, dict)
            or activation.get("qualified") is not True
            or activation.get("active") is not True
            or qualification.get("qualified") is not True
        ):
            continue

        installed_at = 0.0
        result_path = runtime_root / "INSTALLATION_RESULT.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                if (
                    isinstance(result, dict)
                    and isinstance(result.get("installed_at"), (int, float))
                    and not isinstance(result.get("installed_at"), bool)
                ):
                    installed_at = float(result["installed_at"])
            except (OSError, json.JSONDecodeError):
                pass
        if installed_at <= 0:
            try:
                installed_at = spec_path.stat().st_mtime
            except OSError:
                continue
        candidates.append((installed_at, runtime_root.resolve()))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], str(item[1])))
    return candidates[-1][1]


def _default_launcher(python: Path, runtime_root: Path) -> int:
    if not python.is_file():
        raise FileNotFoundError(f"runtime python not found: {python}")
    runtime_root = Path(runtime_root).resolve()
    if not (runtime_root / "RUNTIME_INSTALL_SPEC.json").is_file():
        raise FileNotFoundError(
            f"runtime install spec not found: {runtime_root / 'RUNTIME_INSTALL_SPEC.json'}"
        )
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    env = dict(os.environ)
    env["VERA_RUNTIME_ROOT"] = str(runtime_root)
    process = subprocess.Popen(
        [str(python), "-m", "portal.desktop_host"],
        cwd=str(runtime_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
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
        if config.heartbeat_ttl_seconds <= 0:
            raise ValueError("heartbeat_ttl_seconds must be positive")
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
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            return max(0.0, now - float(observed))
        return max(0.0, now - self.config.heartbeat_path.stat().st_mtime)

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
        heartbeat = self.config.heartbeat_path
        if not heartbeat.is_file():
            pid = self._pid_hint()
            if pid is not None and self._process_alive(pid):
                return RuntimeStatus(
                    RuntimeState.DEGRADED,
                    "heartbeat_missing_process_alive",
                    pid=pid,
                )
            return RuntimeStatus(RuntimeState.OFFLINE, "heartbeat_missing")

        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("heartbeat root must be an object")
            pid = int(payload["pid"])
            runtime_id = str(payload["runtime_id"])
            if not runtime_id:
                raise ValueError("runtime_id is empty")
            age = self._heartbeat_age(payload, now=observed_now)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return RuntimeStatus(RuntimeState.BLOCKED, "heartbeat_invalid")

        if not self._process_alive(pid):
            return RuntimeStatus(
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
        current = self.status(now=now)
        if current.state is not RuntimeState.OFFLINE:
            return current
        pid = self._launcher(
            self.config.python_path,
            self.config.runtime_root,
        )
        return RuntimeStatus(
            RuntimeState.STARTING,
            "runtime_launch_requested",
            pid=pid,
        )
