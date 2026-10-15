from __future__ import annotations

from pathlib import Path

from portal.desktop_installer import (
    _record_activation_state,
    build_logon_task_command,
    launcher_text,
)


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

def _staged_import_paths(runtime_root: Path) -> dict[str, str]:
    expected = {
        "portal": "sources/portal/portal/__init__.py",
        "pre_active": "sources/pre-active/src/pre_active/__init__.py",
        "vera_core": "sources/vera-mono/packages/vera_core/src/vera_core/__init__.py",
        "volition": "sources/volition/src/volition/__init__.py",
    }
    for relative in expected.values():
        path = runtime_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return {name: str(runtime_root / relative) for name, relative in expected.items()}


def test_import_verification_accepts_exact_staged_sources(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace
    import portal.desktop_installer as installer

    imports = _staged_import_paths(tmp_path)
    monkeypatch.setattr(
        installer, "_run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(imports))
    )
    assert installer._verify_imports(tmp_path / "python.exe", tmp_path) == imports


def test_import_verification_rejects_ambient_site_package(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace
    import pytest
    import portal.desktop_installer as installer

    imports = _staged_import_paths(tmp_path)
    imports["portal"] = str(tmp_path.parent / "ambient" / "portal" / "__init__.py")
    monkeypatch.setattr(
        installer, "_run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(imports))
    )
    with pytest.raises(RuntimeError, match="portal.*import origin"):
        installer._verify_imports(tmp_path / "python.exe", tmp_path)


def test_import_verification_rejects_wrong_in_tree_module(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace
    import pytest
    import portal.desktop_installer as installer

    imports = _staged_import_paths(tmp_path)
    imports["vera_core"] = str(tmp_path / "sources/portal/vera_core/__init__.py")
    monkeypatch.setattr(
        installer, "_run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(imports))
    )
    with pytest.raises(RuntimeError, match="vera_core.*import origin"):
        installer._verify_imports(tmp_path / "python.exe", tmp_path)


def test_import_verification_rejects_incomplete_import_map(tmp_path: Path, monkeypatch) -> None:
    import json
    from types import SimpleNamespace
    import pytest
    import portal.desktop_installer as installer

    imports = _staged_import_paths(tmp_path)
    del imports["volition"]
    monkeypatch.setattr(
        installer, "_run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(imports))
    )
    with pytest.raises(RuntimeError, match="volition.*import origin"):
        installer._verify_imports(tmp_path / "python.exe", tmp_path)
