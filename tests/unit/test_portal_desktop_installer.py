from __future__ import annotations

from pathlib import Path
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


def test_launcher_sets_runtime_root_and_invokes_module(tmp_path: Path) -> None:
    root = tmp_path / "Vera Desktop"
    python = root / ".venv" / "Scripts" / "python.exe"

    text = launcher_text(root, python, "portal.desktop_host")

    assert f'set "VERA_RUNTIME_ROOT={root}"' in text
    assert f'"{python}" -m portal.desktop_host' in text


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
