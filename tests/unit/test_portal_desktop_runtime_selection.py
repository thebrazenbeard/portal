from __future__ import annotations

import json
from pathlib import Path

import pytest

from portal.desktop_app import resolve_desktop_runtime_root


def _runtime(base: Path, name: str, *, pid: int, observed_at: float,
             installed: bool = True, loaded: bool = True) -> Path:
    root = base / name
    bridge = root / "bridge"
    bridge.mkdir(parents=True)
    if installed:
        (root / "RUNTIME_INSTALL_SPEC.json").write_text("{}", encoding="utf-8")
    status = {
        "pid": pid, "runtime_id": name, "observed_at": observed_at,
        "state": "ACTIVE",
        "components": {
            key: {"loaded": loaded}
            for key in ("portal", "vera_mono", "pre_active", "volition")
        },
    }
    (bridge / "heartbeat.json").write_text(json.dumps(status), encoding="utf-8")
    return root


def test_selects_only_live_installed_runtime_not_latest_folder(tmp_path: Path) -> None:
    active = _runtime(tmp_path, "20261007-running", pid=11, observed_at=100.0)
    _runtime(tmp_path, "20261008-staged", pid=22, observed_at=100.0)
    _runtime(tmp_path, "20261009-stale", pid=33, observed_at=1.0)
    _runtime(tmp_path, "20261010-unknown", pid=11, observed_at=100.0, installed=False)
    assert resolve_desktop_runtime_root(
        installations_root=tmp_path,
        process_alive=lambda pid: pid == 11,
        now=102.0,
    ) == active


def test_fails_closed_if_two_installed_runtimes_are_active(tmp_path: Path) -> None:
    _runtime(tmp_path, "a", pid=11, observed_at=100.0)
    _runtime(tmp_path, "b", pid=12, observed_at=100.0)
    with pytest.raises(RuntimeError, match="multiple active"):
        resolve_desktop_runtime_root(
            installations_root=tmp_path,
            process_alive=lambda _pid: True,
            now=101.0,
        )


def test_fails_clearly_if_no_active_installation(tmp_path: Path) -> None:
    _runtime(tmp_path, "stale", pid=11, observed_at=1.0)
    with pytest.raises(RuntimeError, match="no active"):
        resolve_desktop_runtime_root(
            installations_root=tmp_path,
            process_alive=lambda _pid: True,
            now=101.0,
        )


def test_explicit_operator_runtime_overrides_discovery(tmp_path: Path) -> None:
    requested = tmp_path / "specific-runtime"
    assert resolve_desktop_runtime_root(
        configured=str(requested), installations_root=tmp_path,
        process_alive=lambda _pid: False, now=101.0,
    ) == requested


def test_blank_explicit_override_is_not_ignored(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VERA_RUNTIME_ROOT"):
        resolve_desktop_runtime_root(
            configured="  ", installations_root=tmp_path,
        )


def test_refresh_does_not_stack_ipc_threads_when_host_is_slow() -> None:
    import threading
    import time

    from portal.desktop_app import PortalDesktopApp, DesktopSnapshot

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._refresh_lock = threading.Lock()
    calls = []
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class SlowViewModel:
        def refresh(self):
            calls.append("called")
            started.set()
            release.wait(timeout=3.0)
            finished.set()
            return DesktopSnapshot("ACTIVE", "x", None, None, (), ())

    class Root:
        def after(self, _delay, _callback):
            pass

    app.view_model = SlowViewModel()
    app.root = Root()
    app.refresh_async()
    try:
        assert started.wait(timeout=2.0)
        app.refresh_async()
        time.sleep(0.1)
        assert calls == ["called"], "overlapping desktop IPC refreshes"
    finally:
        release.set()
    assert finished.wait(timeout=2.0)


def test_unobserved_pending_effects_are_not_misreported_as_none() -> None:
    from portal.desktop_app import PortalDesktopApp, DesktopSnapshot

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    class Label:
        def __init__(self):
            self.value = None
        def set(self, value):
            self.value = value

    app.health_var = Label()
    app.route_var = Label()
    app.effects_var = Label()
    app.autonomous_text = object()
    app.activity_text = object()
    app._set_text = lambda widget, text: None

    app._render_snapshot(DesktopSnapshot(
        "ACTIVE", "runtime-id", None, None, (), (),
    ))
    assert "not reported" in app.effects_var.value.lower()


def test_failed_refresh_callback_preserves_error_after_worker_exits() -> None:
    import threading
    from portal.desktop_app import PortalDesktopApp

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._refresh_lock = threading.Lock()
    queued = []
    notified = threading.Event()

    class FailedViewModel:
        def refresh(self):
            raise OSError("host unavailable")

    class Root:
        def after(self, delay, callback):
            assert delay == 0
            queued.append(callback)
            notified.set()

    class Label:
        value = ""
        def set(self, text):
            self.value = text

    app.view_model = FailedViewModel()
    app.root = Root()
    app.health_var = Label()
    app.refresh_async()
    assert notified.wait(timeout=2.0)
    queued[0]()
    assert app.health_var.value == "Runtime: DEGRADED · OSError: host unavailable"
    assert not app._refresh_lock.locked()
