from __future__ import annotations

import json
from pathlib import Path

from portal.desktop_supervisor import (
    RuntimeState,
    RuntimeSupervisor,
    RuntimeSupervisorConfig,
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


def test_discover_active_runtime_root_selects_newest_qualified_active_install(
    tmp_path: Path,
) -> None:
    from portal import desktop_supervisor

    discover = getattr(desktop_supervisor, "discover_active_runtime_root", None)
    assert callable(discover)

    base = tmp_path / "VeraDesktopRuntime"
    older = base / "older"
    newer = base / "newer"
    inactive = base / "inactive"
    for root, installed_at, active in (
        (older, 100.0, True),
        (newer, 200.0, True),
        (inactive, 300.0, False),
    ):
        root.mkdir(parents=True)
        (root / "RUNTIME_INSTALL_SPEC.json").write_text(
            json.dumps(
                {
                    "schema": "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
                    "activation": {
                        "requested": active,
                        "qualified": active,
                        "active": active,
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / "QUALIFICATION.json").write_text(
            json.dumps({"qualified": active}),
            encoding="utf-8",
        )
        (root / "INSTALLATION_RESULT.json").write_text(
            json.dumps({"installed_at": installed_at}),
            encoding="utf-8",
        )

    assert discover(base) == newer


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
    assert launches[0][1] == tmp_path


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
