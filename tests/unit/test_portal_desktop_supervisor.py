from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from portal.desktop_supervisor import (
    RuntimeState,
    RuntimeSupervisor,
    RuntimeSupervisorConfig,
    process_is_alive,
)


def _write_heartbeat(root: Path, *, pid: int = 77, observed_at: float = 100.0) -> None:
    bridge = root / "bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / "heartbeat.json").write_text(
        json.dumps(
            {
                "schema": "VERA_UNIFIED_RUNTIME_STATUS_V1",
                "runtime_id": "runtime-1",
                "pid": pid,
                "observed_at": observed_at,
                "components": {
                    "vera_mono": {"loaded": True},
                    "portal": {"loaded": True},
                    "pre_active": {"loaded": True},
                    "volition": {"loaded": True},
                },
            }
        ),
        encoding="utf-8",
    )


def _supervisor(root: Path, *, alive: bool) -> RuntimeSupervisor:
    return RuntimeSupervisor(
        RuntimeSupervisorConfig(
            runtime_root=root,
            heartbeat_ttl_seconds=5.0,
        ),
        process_alive=lambda _pid: alive,
    )


def test_missing_heartbeat_is_offline(tmp_path: Path) -> None:
    status = _supervisor(tmp_path, alive=False).status(now=100.0)
    assert status.state is RuntimeState.OFFLINE
    assert status.runtime_id is None
    assert status.reason == "heartbeat_missing"


def test_fresh_live_heartbeat_is_active(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path, observed_at=98.0)
    status = _supervisor(tmp_path, alive=True).status(now=100.0)
    assert status.state is RuntimeState.ACTIVE
    assert status.runtime_id == "runtime-1"
    assert status.heartbeat_age_seconds == 2.0
    assert status.components_loaded is True


def test_stale_live_heartbeat_is_degraded_and_not_relaunched(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path, observed_at=90.0)
    launches: list[tuple[Path, Path]] = []
    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(runtime_root=tmp_path, heartbeat_ttl_seconds=5.0),
        process_alive=lambda _pid: True,
        launcher=lambda python, script: launches.append((python, script)) or 999,
    )
    status = supervisor.ensure_started(now=100.0)
    assert status.state is RuntimeState.DEGRADED
    assert status.reason == "heartbeat_stale_process_alive"
    assert launches == []


def test_dead_pid_is_offline_even_when_heartbeat_is_fresh(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path, observed_at=99.0)
    status = _supervisor(tmp_path, alive=False).status(now=100.0)
    assert status.state is RuntimeState.OFFLINE
    assert status.reason == "runtime_process_not_alive"


def test_invalid_heartbeat_is_blocked_and_not_relaunched(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir(parents=True)
    (bridge / "heartbeat.json").write_text("{", encoding="utf-8")
    launches: list[tuple[Path, Path]] = []
    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(runtime_root=tmp_path),
        process_alive=lambda _pid: False,
        launcher=lambda python, script: launches.append((python, script)) or 999,
    )
    status = supervisor.ensure_started(now=100.0)
    assert status.state is RuntimeState.BLOCKED
    assert status.reason == "heartbeat_invalid"
    assert launches == []


def test_offline_runtime_is_started_once_and_reported_starting(tmp_path: Path) -> None:
    launches: list[tuple[Path, Path]] = []
    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(runtime_root=tmp_path),
        process_alive=lambda _pid: False,
        launcher=lambda python, script: launches.append((python, script)) or 1234,
    )
    status = supervisor.ensure_started(now=100.0)
    assert status.state is RuntimeState.STARTING
    assert status.pid == 1234
    assert status.reason == "runtime_launch_requested"
    assert len(launches) == 1
    assert launches[0][0] == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert launches[0][1] == tmp_path / "host" / "vera_unified_host.py"


def test_missing_component_marks_live_runtime_degraded(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path, observed_at=99.0)
    path = tmp_path / "bridge" / "heartbeat.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["components"]["volition"]["loaded"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = _supervisor(tmp_path, alive=True).status(now=100.0)
    assert status.state is RuntimeState.DEGRADED
    assert status.reason == "component_not_loaded"
    assert status.components_loaded is False


@pytest.mark.parametrize("observed_at", [float("nan"), float("inf"), float("-inf"), 101.0, True, "100", None])
def test_invalid_or_future_heartbeat_time_never_counts_as_healthy(tmp_path: Path, observed_at: object) -> None:
    _write_heartbeat(tmp_path, observed_at=observed_at)
    status = _supervisor(tmp_path, alive=True).status(now=100.0)
    assert status.state is RuntimeState.BLOCKED
    assert status.reason == "heartbeat_invalid"


@pytest.mark.parametrize("pid", [True, 0, -1, "77", 77.5])
def test_invalid_heartbeat_pid_is_blocked(tmp_path: Path, pid: object) -> None:
    _write_heartbeat(tmp_path, pid=pid)
    assert _supervisor(tmp_path, alive=True).status(now=100.0).state is RuntimeState.BLOCKED


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), 0, -1])
def test_supervisor_rejects_invalid_freshness_policy(tmp_path: Path, ttl: float) -> None:
    with pytest.raises(ValueError):
        RuntimeSupervisor(RuntimeSupervisorConfig(tmp_path, ttl))


def test_live_process_with_heartbeat_failure_is_not_active(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path)
    path = tmp_path / "bridge" / "heartbeat.json"
    payload = json.loads(path.read_text())
    payload.update(state="BLOCKED", failure="source_revision_mismatch")
    path.write_text(json.dumps(payload))
    status = _supervisor(tmp_path, alive=True).status(now=100.0)
    assert status.state is RuntimeState.BLOCKED
    assert status.reason == "source_revision_mismatch"


def test_live_launch_reservation_prevents_second_supervisor_launch(tmp_path: Path) -> None:
    launches = []
    kwargs = dict(
        process_alive=lambda pid: pid == 1234,
        launcher=lambda python, script: launches.append((python, script)) or 1234,
    )
    config = RuntimeSupervisorConfig(tmp_path)
    first = RuntimeSupervisor(config, **kwargs).ensure_started(now=100.0)
    second = RuntimeSupervisor(config, **kwargs).ensure_started(now=100.0)
    assert first.state is RuntimeState.STARTING
    assert second.state is RuntimeState.STARTING
    assert second.pid == 1234
    assert len(launches) == 1


def test_overlapping_supervisors_only_launch_once(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    launches = []
    errors = []

    def launcher(python: Path, script: Path) -> int:
        launches.append((python, script))
        entered.set()
        assert release.wait(5)
        return 1234

    def first() -> None:
        try:
            RuntimeSupervisor(RuntimeSupervisorConfig(tmp_path), process_alive=lambda pid: False, launcher=launcher).ensure_started(now=100.0)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    try:
        second = RuntimeSupervisor(RuntimeSupervisorConfig(tmp_path), process_alive=lambda pid: False, launcher=lambda *args: launches.append(args) or 9999).ensure_started(now=100.0)
        assert second.state is RuntimeState.STARTING
        assert len(launches) == 1
    finally:
        release.set()
        thread.join(5)
    assert errors == []


def test_real_current_process_is_alive() -> None:
    assert process_is_alive(os.getpid())


def test_real_exited_process_is_not_alive_even_for_exit_code_259() -> None:
    with subprocess.Popen([sys.executable, "-c", "raise SystemExit(259)"]) as child:
        child.wait(timeout=5)
        assert process_is_alive(child.pid) is False


def test_dead_heartbeat_with_live_new_pid_does_not_launch_again(tmp_path: Path) -> None:
    _write_heartbeat(tmp_path, pid=77)
    (tmp_path / "bridge" / "pid.txt").write_text("1234")
    launches = []
    status = RuntimeSupervisor(
        RuntimeSupervisorConfig(tmp_path),
        process_alive=lambda pid: pid == 1234,
        launcher=lambda *args: launches.append(args) or 9999,
    ).ensure_started(now=100.0)
    assert status.state is RuntimeState.DEGRADED
    assert status.pid == 1234
    assert launches == []


def test_launch_failure_returns_actionable_blocked_state(tmp_path: Path) -> None:
    def fail(_python, _script):
        raise FileNotFoundError("runtime python not found")

    status = RuntimeSupervisor(
        RuntimeSupervisorConfig(tmp_path), process_alive=lambda pid: False,
        launcher=fail,
    ).ensure_started(now=100.0)
    assert status.state is RuntimeState.BLOCKED
    assert "runtime python not found" in status.reason


def test_live_launch_without_heartbeat_eventually_becomes_degraded(tmp_path: Path) -> None:
    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(tmp_path, startup_timeout_seconds=10),
        process_alive=lambda pid: pid == 1234, launcher=lambda *args: 1234,
    )
    supervisor.ensure_started(now=100)
    status = supervisor.status(now=111)
    assert status.state is RuntimeState.DEGRADED
    assert status.reason == "runtime_startup_heartbeat_timeout"


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle ABI")
def test_windows_process_check_uses_pointer_sized_handle(monkeypatch) -> None:
    import ctypes
    from types import SimpleNamespace
    from portal.desktop_supervisor import _windows_process_alive

    class Function:
        def __init__(self, call):
            self.call = call
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.call(*args)

    handle = 2 ** 40 + 123
    closed = []
    kernel = SimpleNamespace(
        OpenProcess=Function(lambda *_args: handle),
        WaitForSingleObject=Function(lambda observed, timeout: 258 if observed == handle else 0),
        CloseHandle=Function(lambda observed: closed.append(observed) or 1),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    assert _windows_process_alive(77)
    assert kernel.OpenProcess.restype is ctypes.wintypes.HANDLE
    assert closed == [handle]

def test_default_launcher_recovers_installed_module_and_retains_logs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from portal.desktop_supervisor import _default_launcher
    python = tmp_path / '.venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    seen = {}
    def launch(args, **kwargs):
        seen.update(args=args, **kwargs)
        kwargs['stderr'].write(b'actionable launch failure\n')
        return SimpleNamespace(pid=1234)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    assert _default_launcher(python, tmp_path / 'host/vera_unified_host.py') == 1234
    assert seen['args'] == [str(python), '-m', 'portal.desktop_host', '--runtime-root', str(tmp_path)]
    assert seen['env']['VERA_RUNTIME_ROOT'] == str(tmp_path)
    assert (tmp_path / 'logs/runtime.stderr.log').read_text() == 'actionable launch failure\n'
    assert seen['stderr'].closed


def test_supervisor_cli_reports_starting_without_claiming_healthy(tmp_path, monkeypatch, capsys):
    import portal.desktop_supervisor as module
    monkeypatch.setattr(module.RuntimeSupervisor, 'ensure_started', lambda self: module.RuntimeStatus(module.RuntimeState.STARTING, 'runtime_launch_requested', pid=99))
    assert module.main(['--runtime-root', str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['state'] == 'STARTING'
    assert report['pid'] == 99
