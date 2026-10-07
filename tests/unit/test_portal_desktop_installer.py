from __future__ import annotations

import json
from pathlib import Path

import pytest

import portal.desktop_installer as desktop_installer
from portal.desktop_install import InstallSpec, RuntimeCognitionTarget, RuntimeSource
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


def test_install_rejects_v1_before_staging_any_runtime_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="legacy",
        sources=(
            RuntimeSource("vera-mono", "x/a", "main", "a" * 40),
            RuntimeSource("portal", "x/b", "main", "b" * 40),
            RuntimeSource("pre-active", "x/c", "main", "c" * 40),
            RuntimeSource("volition", "x/d", "main", "d" * 40),
        ),
    )

    def must_not_stage(*_args, **_kwargs):
        raise AssertionError("V1 rejection must happen before stage preparation")

    monkeypatch.setattr(desktop_installer, "prepare_stage_root", must_not_stage)

    with pytest.raises(RuntimeError, match="V2"):
        desktop_installer.install(
            runtime_root=tmp_path / "runtime",
            spec=spec,
            activate=False,
        )


def test_configure_cognition_target_persists_and_reads_back_exact_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure = getattr(desktop_installer, "_configure_cognition_target", None)
    assert callable(configure)

    target = RuntimeCognitionTarget(
        name="vera-base",
        provider="openai-compatible",
        base_url="http://127.0.0.1:18081/v1",
        model="qwen3.5-4b-local",
        api_key_env=None,
    )
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2",
        install_id="two",
        sources=(
            RuntimeSource("vera-mono", "x/a", "main", "a" * 40),
            RuntimeSource("portal", "x/b", "work/x", "b" * 40),
            RuntimeSource("pre-active", "x/c", "main", "c" * 40),
            RuntimeSource("volition", "x/d", "main", "d" * 40),
        ),
        cognition_target=target,
    )
    observed_calls: list[dict[str, object]] = []

    class Completed:
        stdout = json.dumps(
            {
                "name": "vera-base",
                "provider": "openai-compatible",
                "base_url": "http://127.0.0.1:18081/v1",
                "model": "qwen3.5-4b-local",
                "api_key_env": None,
                "active": True,
            }
        )

    def fake_run(args, *, cwd=None, env=None, capture=False):
        observed_calls.append(
            {
                "args": args,
                "cwd": cwd,
                "capture": capture,
            }
        )
        return Completed()

    monkeypatch.setattr(desktop_installer, "_run", fake_run)
    python = tmp_path / ".venv" / "Scripts" / "python.exe"

    observed = configure(tmp_path, python, spec)

    assert observed["active"] is True
    assert observed["name"] == "vera-base"
    assert len(observed_calls) == 1
    call = observed_calls[0]
    assert call["args"][0] == str(python)
    assert call["args"][1] == "-c"
    assert call["capture"] is True
    assert str(tmp_path / "state" / "pre-active" / "runtime.sqlite3") in call["args"]


def test_spec_from_args_builds_v2_cognition_binding() -> None:
    from types import SimpleNamespace

    args = SimpleNamespace(
        install_id="two",
        portal_ref="work/preactive-provider-v1",
        portal_sha="b" * 40,
        vera_mono_sha="a" * 40,
        pre_active_sha="c" * 40,
        volition_sha="d" * 40,
        cognition_target_name="vera-base",
        cognition_base_url="http://127.0.0.1:18081/v1",
        cognition_model="qwen3.5-4b-local",
        cognition_api_key_env=None,
    )

    spec = desktop_installer._spec_from_args(args)

    assert spec.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"
    assert spec.cognition_target is not None
    assert spec.cognition_target.to_mapping() == {
        "name": "vera-base",
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:18081/v1",
        "model": "qwen3.5-4b-local",
        "api_key_env": None,
    }


def test_main_binds_required_cognition_target_and_defaults_portal_ref_to_main(
    monkeypatch,
    capsys,
) -> None:
    import sys

    observed: dict[str, object] = {}

    def fake_install(*, runtime_root, spec, activate):
        observed["runtime_root"] = runtime_root
        observed["spec"] = spec
        observed["activate"] = activate
        return {"ok": True}

    monkeypatch.setattr(desktop_installer, "install", fake_install)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "portal-desktop-install",
            "--runtime-root",
            r"C:\temp\runtime",
            "--install-id",
            "two",
            "--portal-sha",
            "b" * 40,
            "--vera-mono-sha",
            "a" * 40,
            "--pre-active-sha",
            "c" * 40,
            "--volition-sha",
            "d" * 40,
            "--cognition-target-name",
            "vera-base",
            "--cognition-base-url",
            "http://127.0.0.1:18081/v1",
            "--cognition-model",
            "qwen3.5-4b-local",
        ],
    )

    desktop_installer.main()

    spec = observed["spec"]
    assert spec.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"
    assert spec.source_map()["portal"].ref == "main"
    assert spec.cognition_target is not None
    assert spec.cognition_target.model == "qwen3.5-4b-local"
    assert observed["activate"] is False
    assert json.loads(capsys.readouterr().out) == {"ok": True}
