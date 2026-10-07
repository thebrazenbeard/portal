from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess

import pytest

from portal.desktop_install import InstallSpec, RuntimeSource, StageRootConflict
from portal import desktop_installer as installer

from portal.desktop_installer import (
    _record_activation_state,
    build_logon_task_command,
    launcher_text,
)


@pytest.fixture(autouse=True)
def user_local_test_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path.parent)


def test_launcher_sets_runtime_root_and_invokes_module(tmp_path: Path) -> None:
    root = tmp_path / "Vera Desktop"
    python = root / ".venv" / "Scripts" / "python.exe"

    text = launcher_text(root, python, "portal.desktop_host")

    assert f'set "VERA_RUNTIME_ROOT={root}"' in text
    assert f'"{python}" -m portal.desktop_host' in text


def test_logon_task_command_binds_exact_launcher_path(tmp_path: Path) -> None:
    launcher = tmp_path / "StartVeraRuntime.cmd"

    command = build_logon_task_command(
        task_name="VeraDesktopRuntime-test",
        launcher=launcher,
    )

    assert command[:4] == ["schtasks.exe", "/Create", "/F", "/SC"]
    assert "ONLOGON" in command
    assert "/TN" in command
    assert "VeraDesktopRuntime-test" in command
    assert str(launcher) in command[-1]


def test_logon_task_name_is_not_shell_interpolated(tmp_path: Path) -> None:
    launcher = tmp_path / "Start.cmd"
    try:
        build_logon_task_command(task_name='bad"name', launcher=launcher)
    except ValueError as exc:
        assert "task_name" in str(exc)
    else:
        raise AssertionError("expected invalid task name rejection")


def test_activation_state_updates_runtime_spec_without_touching_sources(tmp_path: Path) -> None:
    path = tmp_path / "RUNTIME_INSTALL_SPEC.json"
    original = {
        "schema": "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        "install_id": "one",
        "sources": [{"component": "portal", "sha": "a" * 40}],
        "activation": {"requested": False, "qualified": False, "active": False},
        "claim_ceiling": "OLD",
    }
    import json
    path.write_text(json.dumps(original), encoding="utf-8")

    _record_activation_state(
        tmp_path,
        requested=True,
        qualified=True,
        active=True,
    )

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["sources"] == original["sources"]
    assert updated["activation"] == {
        "requested": True,
        "qualified": True,
        "active": True,
    }
    assert "ACTIVE_AT_LOGON" in updated["claim_ceiling"]


def _spec() -> InstallSpec:
    return InstallSpec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1", "test", tuple(
        RuntimeSource(name, "thebrazenbeard/" + name, "main", digit * 40)
        for name, digit in zip(("vera-mono", "portal", "pre-active", "volition"), "abcd")
    ))


def _fake_install(monkeypatch, calls: list[str]) -> None:
    monkeypatch.setattr(installer, "_install_sources", lambda root, spec: calls.append("install") or root / ".venv/Scripts/python.exe")
    monkeypatch.setattr(installer, "_verify_imports", lambda *args: calls.append("imports") or {"portal": "p"})
    monkeypatch.setattr(installer, "_check_components", lambda *args: calls.append("check") or {"qualified": True, "components_loaded": True})
    monkeypatch.setattr(installer, "_start_host", lambda *args: calls.append("start"))
    monkeypatch.setattr(installer, "_qualify", lambda *args: calls.append("cognition") or {"qualified": True})


def test_dry_run_has_no_filesystem_or_process_effect(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "not-created"
    monkeypatch.setattr(installer, "_install_sources", lambda *_args: pytest.fail("dry run installed sources"))
    plan = installer.install(runtime_root=root, spec=_spec(), activate=False, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["cognition_policy"] == {"allow_local_no_paid_compute": False}
    assert len(plan["source_map"]) == 4
    assert not root.exists()


def test_default_policy_checks_components_without_invoking_cognition(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    _fake_install(monkeypatch, calls)
    result = installer.install(runtime_root=tmp_path, spec=_spec(), activate=False)
    assert calls == ["install", "imports", "check", "start"]
    payload = json.loads((tmp_path / "RUNTIME_INSTALL_SPEC.json").read_text())
    assert payload["cognition_policy"] == {"allow_local_no_paid_compute": False}
    assert result["qualification"]["local_cognition_qualified"] is False
    shim = (tmp_path / "host/vera_unified_host.py").read_text()
    assert "VERA_RUNTIME_ROOT" in shim
    assert "portal.desktop_host" in shim
    compile(shim, "vera_unified_host.py", "exec")
    assert "-m portal.desktop_supervisor" in (tmp_path / "StartVeraRuntime.cmd").read_text()


def test_explicit_local_cognition_policy_is_persisted_and_qualified(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    _fake_install(monkeypatch, calls)
    result = installer.install(runtime_root=tmp_path, spec=_spec(), activate=False, allow_local_no_paid_compute=True)
    assert calls[-1] == "cognition"
    assert result["qualification"]["local_cognition_qualified"] is True
    payload = json.loads((tmp_path / "RUNTIME_INSTALL_SPEC.json").read_text())
    assert payload["cognition_policy"] == {"allow_local_no_paid_compute": True}


def test_qualified_reinstall_preserves_sources_and_activation(tmp_path: Path, monkeypatch) -> None:
    spec = _spec()
    spec.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    payload = json.loads((tmp_path / "RUNTIME_INSTALL_SPEC.json").read_text())
    payload["cognition_policy"] = {"allow_local_no_paid_compute": False}
    payload["activation"] = {"requested": True, "qualified": True, "active": True}
    path = tmp_path / "RUNTIME_INSTALL_SPEC.json"
    path.write_text(json.dumps(payload))
    prior_bytes = path.read_bytes()
    (tmp_path / "INSTALLATION_RESULT.json").write_text(json.dumps({
        "qualification": {"qualified": True},
        "activation": {"requested": True, "active_at_logon": True, "task_name": "prior"},
    }))
    for name in ("_install_sources", "_start_host", "_verify_imports", "_qualify", "_run"):
        monkeypatch.setattr(installer, name, lambda *_args, **_kwargs: pytest.fail("qualified reinstall mutated the runtime"))
    checked = []
    monkeypatch.setattr(installer, "_verify_existing_binding", lambda *args: checked.append(args))
    result = installer.install(runtime_root=tmp_path, spec=spec, activate=False)
    assert result["reused_installation"] is True
    assert result["activation"]["active_at_logon"] is True
    assert path.read_bytes() == prior_bytes
    assert len(checked) == 1


def test_policy_or_source_change_requires_separate_root(tmp_path: Path) -> None:
    _spec().write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    before = (tmp_path / "RUNTIME_INSTALL_SPEC.json").read_bytes()
    with pytest.raises(StageRootConflict, match="policy"):
        installer.install(runtime_root=tmp_path, spec=_spec(), activate=False, allow_local_no_paid_compute=True)
    assert (tmp_path / "RUNTIME_INSTALL_SPEC.json").read_bytes() == before


def test_failure_is_logged_before_any_activation_claim(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    _fake_install(monkeypatch, calls)
    monkeypatch.setattr(installer, "_check_components", lambda *_args: (_ for _ in ()).throw(RuntimeError("component failed: missing module")))
    monkeypatch.setattr(installer, "_run", lambda *_args, **_kwargs: pytest.fail("must not register activation"))
    with pytest.raises(RuntimeError, match="component failed"):
        installer.install(runtime_root=tmp_path, spec=_spec(), activate=True)
    assert "component failed: missing module" in (tmp_path / "logs/install.log").read_text()
    payload = json.loads((tmp_path / "RUNTIME_INSTALL_SPEC.json").read_text())
    assert payload["activation"]["active"] is False
    assert payload["activation"]["qualified"] is False


def test_component_check_invokes_exact_root_and_requires_success(tmp_path: Path, monkeypatch) -> None:
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"components_loaded": True, "qualified": True}), "")
    monkeypatch.setattr(installer, "_run", run)
    result = installer._check_components(tmp_path / "python.exe", tmp_path)
    assert result["components_loaded"] is True
    assert seen[0][-3:] == ["--runtime-root", str(tmp_path), "--check"]


def test_startup_accepts_no_admitted_route_but_rejects_stopped_worker(tmp_path: Path, monkeypatch) -> None:
    from portal import desktop_supervisor
    from portal.desktop_supervisor import RuntimeState, RuntimeStatus

    status = RuntimeStatus(RuntimeState.BLOCKED, "no_admissible_cognition_route", heartbeat_age_seconds=0.1, components_loaded=True)
    class Supervisor:
        config = desktop_supervisor.RuntimeSupervisorConfig(tmp_path)
        def __init__(self, *_args, **_kwargs):
            pass
        def ensure_started(self):
            return status
    monkeypatch.setattr(desktop_supervisor, "RuntimeSupervisor", Supervisor)
    installer._start_host(tmp_path, tmp_path / "python.exe")
    status = RuntimeStatus(RuntimeState.BLOCKED, "resident_worker_stopped", heartbeat_age_seconds=0.1, components_loaded=True)
    with pytest.raises(RuntimeError, match="resident_worker_stopped"):
        installer._start_host(tmp_path, tmp_path / "python.exe")


def test_qualified_activation_checks_fresh_runtime_before_registration(tmp_path: Path, monkeypatch) -> None:
    spec = _spec()
    spec.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    installer._record_activation_state(tmp_path, requested=False, qualified=True, active=False)
    (tmp_path / "INSTALLATION_RESULT.json").write_text(json.dumps({
        "imports": {}, "qualification": {"qualified": True}, "activation": {"active_at_logon": False},
    }))
    calls = []
    monkeypatch.setattr(installer, "_verify_existing_binding", lambda *_args: calls.append("source-binding"))
    monkeypatch.setattr(installer, "_check_existing_runtime", lambda *_args: calls.append("fresh-health"))
    monkeypatch.setattr(installer, "_install_sources", lambda *_args: pytest.fail("qualified activation reinstalled sources"))
    def run(args, **_kwargs):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, "VeraDesktopRuntime-test", "")
    monkeypatch.setattr(installer, "_run", run)
    installer.install(runtime_root=tmp_path, spec=spec, activate=True)
    assert calls == ["source-binding", "fresh-health", "/Create", "/Query"]


def test_running_activation_check_observes_bridge_without_opening_component_stores(tmp_path: Path, monkeypatch) -> None:
    from portal import desktop_supervisor, desktop_ipc
    from portal.desktop_supervisor import RuntimeState, RuntimeStatus
    status = RuntimeStatus(RuntimeState.ACTIVE, "runtime_healthy", heartbeat_age_seconds=0.1, components_loaded=True)
    class Supervisor:
        config = desktop_supervisor.RuntimeSupervisorConfig(tmp_path)
        def __init__(self, *_args, **_kwargs):
            pass
        def status(self):
            return status
    class Client:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, command):
            assert command == "desktop_status"
            return {"source_manifest": _spec().to_mapping(), "protected_effect_authority": False}
    monkeypatch.setattr(desktop_supervisor, "RuntimeSupervisor", Supervisor)
    monkeypatch.setattr(desktop_ipc, "FileBridgeClient", Client)
    monkeypatch.setattr(installer, "_check_components", lambda *_args: pytest.fail("duplicate component stores opened"))
    installer._check_existing_runtime(tmp_path, tmp_path / "python.exe", _spec())


@pytest.mark.skipif(os.name != "nt", reason="Windows native bootstrap")
def test_windows_bootstrap_dry_run_uses_frozen_revisions_and_does_not_create_root(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/Install-PortalVera.ps1"
    root = tmp_path / "not-created"
    completed = subprocess.run([
        "powershell.exe", "-NoProfile", "-File", str(script),
        "-InstallId", "dry-test", "-RuntimeRoot", str(root),
        "-Python", __import__("sys").executable,
        "-PortalSha", "b" * 40, "-VeraMonoSha", "a" * 40,
        "-PreActiveSha", "c" * 40, "-VolitionSha", "d" * 40,
        "-DryRun", "-AllowLocalNoPaidCompute",
    ], text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["source_map"]["portal"]["sha"] == "b" * 40
    assert plan["cognition_policy"]["allow_local_no_paid_compute"] is True
    assert plan["dry_run"] is True
    assert not root.exists()
