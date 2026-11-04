from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from portal import desktop_installer
from portal.desktop_install import InstallSpec, RuntimeSource

from portal.desktop_installer import (
    _record_activation_state,
    build_logon_task_command,
    launcher_text,
)


def _spec() -> InstallSpec:
    return InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="desktop-v1",
        sources=tuple(
            RuntimeSource(name, f"thebrazenbeard/{name}", "main", char * 40)
            for name, char in zip(
                ("vera-mono", "portal", "pre-active", "volition"), "abcd"
            )
        ),
    )


def _meso_spec() -> InstallSpec:
    original = _spec()
    return InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2",
        install_id="desktop-meso-v2",
        sources=original.sources + (
            RuntimeSource("meso-crct", "thebrazenbeard/meso-crct", "main", "e" * 40),
        ),
    )


def test_exact_clone_disables_global_autocrlf_for_pinned_bytes(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "fixture-source"
    source.mkdir()
    (source / "runtime.py").write_bytes(b"line1\nline2\n")
    config = tmp_path / "git-global-config"
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(config)}

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], env=env, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("config", "--file", str(config), "core.autocrlf", "true")
    git("-C", str(source), "init", "--quiet")
    git("-C", str(source), "add", ".")
    git("-C", str(source), "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "LF file")
    head = git("-C", str(source), "rev-parse", "HEAD")

    plain = tmp_path / "plain-global-checkout"
    git("clone", "--no-checkout", str(source), str(plain))
    git("-C", str(plain), "checkout", "--detach", head)
    assert (plain / "runtime.py").read_bytes() == b"line1\r\nline2\r\n"

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    real_run = desktop_installer._run

    def local_clone(args, **kwargs):
        mapped = list(args)
        if "clone" in mapped:
            mapped[-2] = str(source)
        return real_run(mapped, **kwargs)

    monkeypatch.setattr(desktop_installer, "_run", local_clone)
    pinned = tmp_path / "exact-pinned-checkout"
    desktop_installer._clone_exact(
        RuntimeSource("portal", "thebrazenbeard/portal", "main", head), pinned,
    )
    assert (pinned / "runtime.py").read_bytes() == b"line1\nline2\n"


def test_launcher_sets_runtime_root_and_invokes_module(tmp_path: Path) -> None:
    root = tmp_path / "Vera Desktop"
    python = root / ".venv" / "Scripts" / "python.exe"

    text = launcher_text(root, python, "portal.desktop_host")

    assert f'set "VERA_RUNTIME_ROOT={root}"' in text
    assert f'"{python}" -B -m portal.desktop_host' in text
    assert 'PYTHONDONTWRITEBYTECODE=1' in text


@pytest.mark.parametrize("unsafe", ["%PATH%", 'bad"quote', "bad!bang", "bad^caret", "a&b", "a|b", "a>b", "a<b", "new\nline", "é"])
def test_launcher_rejects_paths_with_batch_expansion_or_encoding_hazards(
    tmp_path: Path, unsafe: str,
) -> None:
    root = tmp_path / unsafe
    with pytest.raises(ValueError, match="launcher path"):
        launcher_text(root, root / "python.exe", "portal.desktop_host")


def test_launcher_rejects_an_unapproved_module(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported .* module"):
        launcher_text(tmp_path, tmp_path / "python.exe", "os")


def test_installer_rejects_unsafe_path_before_staging(tmp_path: Path) -> None:
    root = tmp_path / "a%unexpected%runtime"
    with pytest.raises(ValueError, match="launcher path"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=False)
    assert not root.exists()


def test_installer_resolves_runtime_root_before_staging(monkeypatch, tmp_path: Path) -> None:
    observed = {}

    def stop_before_staging(runtime_root, spec):
        observed["runtime_root"] = runtime_root
        raise RuntimeError("staging stopped")

    monkeypatch.setattr(desktop_installer, "prepare_stage_root", stop_before_staging)
    with pytest.raises(RuntimeError, match="staging stopped"):
        desktop_installer.install(
            runtime_root=Path("relative-runtime"), spec=_spec(), activate=False,
        )
    assert observed["runtime_root"].is_absolute()


def test_installer_cli_defaults_to_current_main_ref_without_installing(
    monkeypatch, tmp_path: Path, capsys,
) -> None:
    import sys

    seen = {}

    def fake_install(*, runtime_root, spec, activate):
        seen["spec"] = spec
        seen["activate"] = activate
        return {"qualified": False, "test_only": True}

    monkeypatch.setattr(desktop_installer, "install", fake_install)
    monkeypatch.setattr(sys, "argv", [
        "desktop_installer", "--runtime-root", str(tmp_path),
        "--install-id", "test-main", "--portal-sha", "a" * 40,
        "--vera-mono-sha", "b" * 40, "--pre-active-sha", "c" * 40,
        "--volition-sha", "d" * 40,
    ])
    desktop_installer.main()
    assert seen["spec"].source_map()["portal"].ref == "main"
    assert seen["activate"] is False
    assert '"test_only": true' in capsys.readouterr().out


def test_installer_cli_requires_explicit_meso_sha_for_v2(monkeypatch, tmp_path: Path) -> None:
    import sys

    seen = {}

    def fake_install(*, runtime_root, spec, activate):
        seen.update(spec=spec, activate=activate)
        return {"test_only": True}

    monkeypatch.setattr(desktop_installer, "install", fake_install)
    monkeypatch.setattr(sys, "argv", [
        "desktop_installer", "--runtime-root", str(tmp_path),
        "--install-id", "test-meso", "--portal-sha", "a" * 40,
        "--vera-mono-sha", "b" * 40, "--pre-active-sha", "c" * 40,
        "--volition-sha", "d" * 40, "--meso-sha", "e" * 40,
    ])
    desktop_installer.main()
    assert seen["spec"].schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"
    assert seen["spec"].sources[-1].component == "meso-crct"
    assert seen["activate"] is False


def test_v2_installs_meso_source_and_verifies_import(monkeypatch, tmp_path: Path) -> None:
    import json
    from types import SimpleNamespace

    staged = []
    executed = []
    module_for_component = {
        "vera-mono": "vera_core", "portal": "portal",
        "pre-active": "pre_active", "volition": "volition", "meso-crct": "meso_crct",
    }
    imported_paths = {}

    def fake_clone(source, destination):
        staged.append((source.component, destination))
        destination.mkdir(parents=True)
        path = destination / "staged_module.py"
        path.write_text("# exact staged source\n", encoding="utf-8")
        imported_paths[module_for_component[source.component]] = str(path)

    monkeypatch.setattr(desktop_installer, "_clone_exact", fake_clone)
    python = tmp_path / "python.exe"
    monkeypatch.setattr(desktop_installer, "_ensure_venv", lambda root: python)

    def fake_run(args, **kwargs):
        executed.append(args)
        if kwargs.get("capture"):
            return SimpleNamespace(stdout=json.dumps(imported_paths))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    spec = _meso_spec()
    (tmp_path / "RUNTIME_INSTALL_SPEC.json").write_text(
        json.dumps(spec.to_mapping()), encoding="utf-8",
    )
    assert desktop_installer._install_sources(tmp_path, spec) == python
    assert [name for name, _ in staged] == [
        "vera-mono", "portal", "pre-active", "volition", "meso-crct",
    ]
    assert str(tmp_path / "sources" / "meso-crct") in executed[0]
    imported = desktop_installer._verify_imports(python, tmp_path)
    assert imported["meso_crct"] == imported_paths["meso_crct"]
    assert "meso_crct" in executed[-1][-1]


def test_v2_rejects_import_shadowed_outside_exact_stage(monkeypatch, tmp_path: Path) -> None:
    import json
    from types import SimpleNamespace

    spec = _meso_spec()
    (tmp_path / "RUNTIME_INSTALL_SPEC.json").write_text(
        json.dumps(spec.to_mapping()), encoding="utf-8",
    )
    imported = {}
    for component, module in (
        ("vera-mono", "vera_core"), ("portal", "portal"),
        ("pre-active", "pre_active"), ("volition", "volition"),
        ("meso-crct", "meso_crct"),
    ):
        root = tmp_path / "sources" / component
        root.mkdir(parents=True)
        module_path = root / "module.py"
        module_path.write_text("# module\n", encoding="utf-8")
        imported[module] = str(module_path)
    outside = tmp_path / "elsewhere" / "meso_crct.py"
    outside.parent.mkdir()
    outside.write_text("# poisoned\n", encoding="utf-8")
    imported["meso_crct"] = str(outside)
    monkeypatch.setattr(
        desktop_installer, "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(imported)),
    )
    with pytest.raises(RuntimeError, match="escaped exact staged source: meso_crct"):
        desktop_installer._verify_imports(tmp_path / "python.exe", tmp_path)


def test_v2_rejects_symlinked_source_root_even_when_module_appears_contained(
    monkeypatch, tmp_path: Path,
) -> None:
    import json
    from types import SimpleNamespace

    spec = _meso_spec()
    (tmp_path / "RUNTIME_INSTALL_SPEC.json").write_text(
        json.dumps(spec.to_mapping()), encoding="utf-8",
    )
    imported = {}
    for component, module in (
        ("vera-mono", "vera_core"), ("portal", "portal"),
        ("pre-active", "pre_active"), ("volition", "volition"),
    ):
        source = tmp_path / "sources" / component
        source.mkdir(parents=True)
        path = source / "module.py"
        path.write_text("# source\n", encoding="utf-8")
        imported[module] = str(path)
    external = tmp_path / "external" / "meso-crct"
    external.mkdir(parents=True)
    (external / "module.py").write_text("# not staged\n", encoding="utf-8")
    aliased = tmp_path / "sources" / "meso-crct"
    try:
        aliased.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("source symlinks unsupported on this host")
    imported["meso_crct"] = str(aliased / "module.py")
    monkeypatch.setattr(
        desktop_installer, "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(imported)),
    )
    with pytest.raises(RuntimeError, match="import path cannot be verified: meso_crct"):
        desktop_installer._verify_imports(tmp_path / "python.exe", tmp_path)


def test_qualification_failure_stops_staged_host_and_never_activates(
    monkeypatch, tmp_path: Path,
) -> None:
    class StartedProcess:
        def __init__(self):
            self.terminated = 0
            self.waited = 0

        def poll(self):
            return None

        def terminate(self):
            self.terminated += 1

        def wait(self, timeout):
            self.waited += 1

    child = StartedProcess()
    monkeypatch.setattr(
        desktop_installer, "_install_sources", lambda runtime_root, spec: tmp_path / "python.exe",
    )
    monkeypatch.setattr(desktop_installer, "_verify_imports", lambda python, root: {})
    monkeypatch.setattr(
        desktop_installer, "write_attestation",
        lambda runtime_root, spec: {"schema": "TEST", "sha256": "0" * 64},
    )
    monkeypatch.setattr(desktop_installer, "_start_host", lambda root, python: child)
    monkeypatch.setattr(
        desktop_installer, "_qualify",
        lambda root, python: (_ for _ in ()).throw(RuntimeError("MESO admission failed")),
    )
    monkeypatch.setattr(
        desktop_installer, "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unqualified runtime must not register a task")
        ),
    )
    runtime = tmp_path / "fresh-runtime"
    with pytest.raises(RuntimeError, match="MESO admission failed"):
        desktop_installer.install(runtime_root=runtime, spec=_meso_spec(), activate=True)
    assert child.terminated == 1
    assert child.waited == 1
    assert not (runtime / "INSTALLATION_RESULT.json").exists()
    assert not (runtime / "PortalDesktop.cmd").exists()
    spec = InstallSpec.read(runtime / "RUNTIME_INSTALL_SPEC.json")
    assert spec.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"


def test_source_drift_after_qualification_stops_host_before_activation(
    monkeypatch, tmp_path: Path,
) -> None:
    class Started:
        def __init__(self):
            self.terminated = False
        def poll(self):
            return None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            return 0
    child = Started()
    monkeypatch.setattr(desktop_installer, "_install_sources", lambda root, spec: tmp_path / "python.exe")
    monkeypatch.setattr(desktop_installer, "_verify_imports", lambda python, root: {})
    monkeypatch.setattr(desktop_installer, "write_attestation", lambda root, spec: {"schema": "TEST", "sha256": "0" * 64})
    monkeypatch.setattr(desktop_installer, "_start_host", lambda root, python: child)
    monkeypatch.setattr(desktop_installer, "_qualify", lambda root, python: {"qualified": True})
    monkeypatch.setattr(
        desktop_installer, "verify_attestation",
        lambda root, spec: (_ for _ in ()).throw(RuntimeError("source attestation mismatch")),
    )
    monkeypatch.setattr(desktop_installer, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("drifted runtime must not register a task")
    ))
    runtime = tmp_path / "staged-runtime"
    with pytest.raises(RuntimeError, match="source attestation mismatch"):
        desktop_installer.install(runtime_root=runtime, spec=_spec(), activate=True)
    assert child.terminated
    assert not (runtime / "INSTALLATION_RESULT.json").exists()
    assert not (runtime / "StartVeraRuntime.cmd").exists()


def test_unqualified_staged_host_timeout_escalates_to_owned_process_kill() -> None:
    import subprocess

    class HangingChild:
        def __init__(self):
            self.actions = []

        def poll(self):
            return None

        def terminate(self):
            self.actions.append("terminate")

        def kill(self):
            self.actions.append("kill")

        def wait(self, *, timeout):
            self.actions.append("wait")
            if "kill" not in self.actions:
                raise subprocess.TimeoutExpired("portal.desktop_host", timeout)
            return 0

    child = HangingChild()
    desktop_installer._terminate_unqualified_host(child)
    assert child.actions == ["terminate", "wait", "kill", "wait"]


@pytest.mark.parametrize("preexisting", [
    "dirty-tracked", "untracked", "ignored", "same-head-git", "symlink",
])
def test_clone_rejects_preexisting_source_before_any_git_command(
    monkeypatch, tmp_path: Path, preexisting: str,
) -> None:
    destination = tmp_path / "sources" / "portal"
    if preexisting == "symlink":
        target = tmp_path / "elsewhere"
        target.mkdir()
        destination.parent.mkdir(parents=True)
        try:
            destination.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlink creation not permitted on this host")
    else:
        destination.mkdir(parents=True)
        (destination / ".git").mkdir()
        if preexisting == "dirty-tracked":
            (destination / "tracked.py").write_text("modified", encoding="utf-8")
        if preexisting == "untracked":
            (destination / "extra.py").write_text("untracked", encoding="utf-8")
        if preexisting == "ignored":
            (destination / "ignored.py").write_text("ignored", encoding="utf-8")

    def refuse_run(*_args, **_kwargs):
        raise AssertionError("no git command should execute against existing source")

    monkeypatch.setattr(desktop_installer, "_run", refuse_run)
    expected = "symlink, junction, or reparse" if preexisting == "symlink" else "new staged install leaf"
    with pytest.raises(RuntimeError, match=expected):
        desktop_installer._clone_exact(
            RuntimeSource("portal", "thebrazenbeard/portal", "main", "a" * 40),
            destination,
        )


def test_multi_component_source_preflight_blocks_any_clone(
    monkeypatch, tmp_path: Path,
) -> None:
    existing = tmp_path / "sources" / "volition"
    existing.mkdir(parents=True)
    (existing / "extra.py").write_text("leftover", encoding="utf-8")
    monkeypatch.setattr(
        desktop_installer, "_clone_exact",
        lambda *_args: (_ for _ in ()).throw(AssertionError("clone must not start")),
    )
    with pytest.raises(RuntimeError, match="new staged install leaf"):
        desktop_installer._install_sources(tmp_path, _spec())


def test_repeated_install_refuses_existing_sources_without_erasing_activation_receipt(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "VeraDesktop"
    destination = runtime_root / "sources" / "portal"
    destination.mkdir(parents=True)
    installed = runtime_root / "RUNTIME_INSTALL_SPEC.json"
    installed.write_text(
        '{"activation":{"active":true},"evidence":"original"}\n',
        encoding="utf-8",
    )
    before = installed.read_bytes()

    with pytest.raises(RuntimeError, match="new staged install leaf"):
        desktop_installer.install(runtime_root=runtime_root, spec=_spec(), activate=False)

    assert installed.read_bytes() == before


@pytest.mark.parametrize("relative", [
    "sources", "state", "state/cognition", "bridge", "bridge/requests",
    "bridge/responses", "host", "logs", "RUNTIME_INSTALL_SPEC.json",
    "INSTALLATION_RESULT.json", "PortalDesktop.cmd",
])
def test_installer_rejects_prepositioned_runtime_redirects_before_any_write(
    tmp_path: Path, relative: str,
) -> None:
    root = tmp_path / "chosen-runtime"
    root.mkdir()
    link = root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-evidence"
    if link.suffix in (".json", ".cmd"):
        outside.write_text("unchanged", encoding="utf-8")
        directory = False
    else:
        outside.mkdir()
        directory = True
    try:
        link.symlink_to(outside, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    with pytest.raises(RuntimeError, match="not empty"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=False)
    assert outside.exists()
    if not directory:
        assert outside.read_text(encoding="utf-8") == "unchanged"


def test_installer_rejects_symlinked_runtime_root_and_parent(tmp_path: Path) -> None:
    target = tmp_path / "outside-target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    for root in (link, link / "nested-fresh-leaf"):
        with pytest.raises(RuntimeError, match="symlink, junction, or reparse"):
            desktop_installer.install(runtime_root=root, spec=_spec(), activate=False)
    assert not (target / "nested-fresh-leaf").exists()


def test_fresh_empty_runtime_leaf_is_accepted_without_host_execution(tmp_path: Path) -> None:
    leaf = tmp_path / "empty-runtime"
    leaf.mkdir()
    desktop_installer._require_fresh_runtime_leaf(leaf)
    desktop_installer._require_fresh_runtime_leaf(tmp_path / "absent-runtime")


@pytest.mark.parametrize("status,accepted", [
    ("", True),
    (" M tracked.py\n", False),
    ("?? injected.py\n", False),
    ("!! ignored.py\n", False),
])
def test_fresh_exact_checkout_must_be_clean_before_pip(
    monkeypatch, tmp_path: Path, status: str, accepted: bool,
) -> None:
    source = RuntimeSource("portal", "thebrazenbeard/portal", "main", "a" * 40)
    destination = tmp_path / "sources" / "portal"
    observed = []

    def fake_run(args, **kwargs):
        observed.append(args)
        if args[:2] == ["git", "clone"]:
            destination.mkdir(parents=True)
        if "rev-parse" in args:
            return SimpleNamespace(stdout=source.sha + "\n")
        if "status" in args:
            return SimpleNamespace(stdout=status)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    if accepted:
        desktop_installer._clone_exact(source, destination)
    else:
        with pytest.raises(RuntimeError, match="modified, untracked, or ignored"):
            desktop_installer._clone_exact(source, destination)
    assert ["git", "-C", str(destination), "status", "--porcelain=v1",
            "--untracked-files=all", "--ignored=matching"] in observed


def test_logon_task_command_binds_exact_launcher_path(tmp_path: Path) -> None:
    launcher = tmp_path / "StartVeraRuntime.cmd"

    command = build_logon_task_command(
        task_name="VeraDesktopRuntime-test",
        launcher=launcher,
    )

    assert command[:3] == ["schtasks.exe", "/Create", "/SC"]
    assert "/F" not in command
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


def test_logon_task_launcher_path_cannot_expand_environment_variables(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="launcher path"):
        build_logon_task_command(
            task_name="VeraDesktopRuntime-test",
            launcher=tmp_path / "%COMSPEC%" / "StartVeraRuntime.cmd",
        )


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



def _mock_qualified_activation(monkeypatch):
    """Exercise registration without cloning, installing, or running a real host."""
    class OwnedHost:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    host = OwnedHost()
    monkeypatch.setattr(
        desktop_installer, "_install_sources",
        lambda runtime_root, spec: runtime_root / ".venv" / "Scripts" / "python.exe",
    )
    monkeypatch.setattr(desktop_installer, "_verify_imports", lambda *_: {})
    monkeypatch.setattr(
        desktop_installer, "write_attestation",
        lambda *_: {"schema": "TEST", "sha256": "0" * 64},
    )
    monkeypatch.setattr(desktop_installer, "verify_attestation", lambda *_: {})
    monkeypatch.setattr(desktop_installer, "_start_host", lambda *_: host)
    monkeypatch.setattr(desktop_installer, "_qualify", lambda *_: {"qualified": True})
    return host


def _task_inventory(*names):
    return "".join(f'"\\{name}","N/A","Ready"\n' for name in names)


def test_logon_inventory_fails_closed_on_existing_name_and_preserves_task(
    monkeypatch,
) -> None:
    commands = []
    existing = _task_inventory("Microsoft\\Windows\\Defrag\\ScheduledDefrag",
                               "VeraDesktopRuntime-DESKTOP-V1")

    def fake_run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout=existing)

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    with pytest.raises(RuntimeError, match="already exists"):
        desktop_installer._require_unclaimed_logon_task("VeraDesktopRuntime-desktop-v1")
    assert len(commands) == 1
    assert commands[0] == ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"]
    assert not any("/Create" in cmd for cmd in commands)


@pytest.mark.parametrize("failure", ["error", "empty", "malformed"])
def test_logon_inventory_fails_closed_on_uncertain_name(
    monkeypatch, failure: str,
) -> None:
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if failure == "error":
            raise subprocess.CalledProcessError(5, args)
        return SimpleNamespace(stdout="" if failure == "empty" else "not CSV")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    with pytest.raises(RuntimeError, match="refusing logon activation"):
        desktop_installer._require_unclaimed_logon_task("VeraDesktopRuntime-new")
    assert len(commands) == 1
    assert not any("/Create" in cmd for cmd in commands)


def test_task_create_is_noninteractive_at_subprocess_boundary(monkeypatch) -> None:
    observed = {}

    def fake_run(args, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(desktop_installer.subprocess, "run", fake_run)
    desktop_installer._run(["schtasks.exe", "/Create"], noninteractive=True)
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["check"] is True


def test_activate_existing_task_never_creates_or_changes_activation(
    monkeypatch, tmp_path: Path,
) -> None:
    host = _mock_qualified_activation(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=_task_inventory("VeraDesktopRuntime-desktop-v1"))

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    root = tmp_path / "fresh-staged-runtime"
    with pytest.raises(RuntimeError, match="already exists"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert calls == [["schtasks.exe", "/Query", "/FO", "CSV", "/NH"]]
    assert host.terminated
    assert not (root / "INSTALLATION_RESULT.json").exists()
    spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
    assert not spec.to_mapping()["activation"].get("active", False)


def test_activation_creation_race_keeps_original_task_and_no_active_receipt(
    monkeypatch, tmp_path: Path,
) -> None:
    host = _mock_qualified_activation(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if "/Create" in args:
            assert "/F" not in args
            assert kwargs["noninteractive"] is True
            raise subprocess.CalledProcessError(1, args)  # Another actor won.
        return SimpleNamespace(stdout=_task_inventory("UnrelatedTask"))

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    root = tmp_path / "fresh-staged-runtime"
    with pytest.raises(subprocess.CalledProcessError):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert host.terminated
    assert sum("/Create" in args for args, _ in calls) == 1
    assert not (root / "INSTALLATION_RESULT.json").exists()
    spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
    assert not spec.to_mapping()["activation"].get("active", False)


def test_activation_postcreate_readback_failure_is_not_recorded_active(
    monkeypatch, tmp_path: Path,
) -> None:
    host = _mock_qualified_activation(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "/FO" in args and "CSV" in args:
            return SimpleNamespace(stdout=_task_inventory("UnrelatedTask"))
        if "/Create" in args:
            return SimpleNamespace(stdout="SUCCESS")
        if "/FO" in args and "LIST" in args:
            return SimpleNamespace(stdout="TaskName: \\UnrelatedTask\n")
        raise AssertionError("unexpected scheduler invocation")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    root = tmp_path / "fresh-staged-runtime"
    with pytest.raises(RuntimeError, match="could not be read back"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert host.terminated
    assert sum("/Create" in cmd for cmd in calls) == 1
    assert not (root / "INSTALLATION_RESULT.json").exists()
    spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
    assert not spec.to_mapping()["activation"].get("active", False)


def test_activation_fresh_task_passes_noninteractive_create_and_records_result(
    monkeypatch, tmp_path: Path,
) -> None:
    host = _mock_qualified_activation(monkeypatch)
    calls = []
    target = "VeraDesktopRuntime-desktop-v1"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if "/FO" in args and "CSV" in args:
            return SimpleNamespace(stdout=_task_inventory("UnrelatedTask"))
        if "/Create" in args:
            assert "/F" not in args
            assert kwargs["noninteractive"] is True
            return SimpleNamespace(stdout="SUCCESS")
        if "/FO" in args and "LIST" in args:
            return SimpleNamespace(stdout="TaskName: \\" + target + "\n")
        raise AssertionError("unexpected scheduler invocation")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    root = tmp_path / "fresh-staged-runtime"
    result = desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert result["activation"]["active_at_logon"] is True
    assert result["activation"]["task_name"] == target
    assert len([args for args, _ in calls if "/Create" in args]) == 1
    assert not host.terminated
    assert (root / "INSTALLATION_RESULT.json").exists()


def test_exact_task_readback_ignores_launcher_path_substring() -> None:
    target = "VeraDesktopRuntime-desktop-v1"
    output = (
        "TaskName: \\UnrelatedTask\n"
        "Task To Run: C:\\Temp\\VeraDesktopRuntime-desktop-v1.cmd\n"
    )
    with pytest.raises(RuntimeError, match="could not be read back exactly"):
        desktop_installer._require_exact_logon_task_readback(target, output)


def test_exact_task_readback_accepts_uppercase_exact_name() -> None:
    desktop_installer._require_exact_logon_task_readback(
        "VeraDesktopRuntime-desktop-v1",
        "HostName: TEST-PC\nTaskName: \\VERADESKTOPRUNTIME-DESKTOP-V1\n"
        "Task To Run: C:\\Trusted\\StartVeraRuntime.cmd\n",
    )


def test_postregistration_spec_permission_error_creates_uncertain_marker_and_stops_host(
    monkeypatch, tmp_path: Path,
) -> None:
    import json

    host = _mock_qualified_activation(monkeypatch)
    target = "VeraDesktopRuntime-desktop-v1"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "/FO" in args and "CSV" in args:
            return SimpleNamespace(stdout=_task_inventory("UnrelatedTask"))
        if "/Create" in args:
            return SimpleNamespace(stdout="SUCCESS")
        if "/FO" in args and "LIST" in args:
            return SimpleNamespace(stdout="TaskName: \\" + target + "\n")
        raise AssertionError("unexpected task action")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    monkeypatch.setattr(
        desktop_installer, "_record_activation_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("spec locked")),
    )
    root = tmp_path / "fresh-staged-runtime"
    with pytest.raises(PermissionError, match="spec locked"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert host.terminated
    assert sum("/Create" in cmd for cmd in calls) == 1
    assert not (root / "INSTALLATION_RESULT.json").exists()
    saved_spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
    assert not saved_spec.to_mapping()["activation"].get("active", False)
    marker = json.loads((root / "TASK_REGISTRATION_UNCERTAIN.json").read_text())
    assert marker["task_name"] == target
    assert marker["active"] is False
    assert marker["state"] == "UNCERTAIN_REQUIRES_MANUAL_RECONCILIATION"


def test_postregistration_receipt_failure_rolls_back_active_spec_to_uncertain(
    monkeypatch, tmp_path: Path,
) -> None:
    import json

    host = _mock_qualified_activation(monkeypatch)
    target = "VeraDesktopRuntime-desktop-v1"

    def fake_run(args, **kwargs):
        if "/FO" in args and "CSV" in args:
            return SimpleNamespace(stdout=_task_inventory("UnrelatedTask"))
        if "/Create" in args:
            return SimpleNamespace(stdout="SUCCESS")
        if "/FO" in args and "LIST" in args:
            return SimpleNamespace(stdout="TaskName: \\" + target + "\n")
        raise AssertionError("unexpected task action")

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    original_write = Path.write_text

    def deny_result(self, *args, **kwargs):
        if self.name == "INSTALLATION_RESULT.json":
            raise PermissionError("result locked")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", deny_result)
    root = tmp_path / "fresh-staged-runtime"
    with pytest.raises(PermissionError, match="result locked"):
        desktop_installer.install(runtime_root=root, spec=_spec(), activate=True)
    assert host.terminated
    assert not (root / "INSTALLATION_RESULT.json").exists()
    saved_spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
    assert saved_spec.to_mapping()["activation"]["active"] is False
    marker = json.loads((root / "TASK_REGISTRATION_UNCERTAIN.json").read_text())
    assert marker["active"] is False
