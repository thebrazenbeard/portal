from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from .desktop_install import InstallSpec, RuntimeSource, prepare_stage_root


_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


def launcher_text(runtime_root: Path, python: Path, module: str) -> str:
    return (
        "@echo off\r\n"
        f'set "VERA_RUNTIME_ROOT={runtime_root}"\r\n'
        f'"{python}" -m {module}\r\n'
    )


def build_logon_task_command(
    *,
    task_name: str,
    launcher: Path,
) -> list[str]:
    if not _TASK_NAME_RE.fullmatch(task_name):
        raise ValueError("task_name contains unsupported characters")
    return [
        "schtasks.exe",
        "/Create",
        "/F",
        "/SC",
        "ONLOGON",
        "/TN",
        task_name,
        "/TR",
        str(Path(launcher).resolve()),
    ]


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def _clone_exact(source: RuntimeSource, destination: Path) -> None:
    if not (destination / ".git").is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                f"https://github.com/{source.repository}.git",
                str(destination),
            ]
        )
    _run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--no-tags",
            "origin",
            source.sha,
        ]
    )
    _run(
        [
            "git",
            "-C",
            str(destination),
            "checkout",
            "--detach",
            source.sha,
        ]
    )
    observed = _run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        capture=True,
    ).stdout.strip()
    if observed != source.sha:
        raise RuntimeError(
            f"{source.component} checkout mismatch: "
            f"expected {source.sha}, got {observed}"
        )


def _venv_python(runtime_root: Path) -> Path:
    return runtime_root / ".venv" / "Scripts" / "python.exe"


def _ensure_venv(runtime_root: Path) -> Path:
    python = _venv_python(runtime_root)
    if not python.is_file():
        _run([sys.executable, "-m", "venv", str(runtime_root / ".venv")])
    if not python.is_file():
        raise RuntimeError("isolated Python environment was not created")
    return python


def _install_sources(runtime_root: Path, spec: InstallSpec) -> Path:
    sources = runtime_root / "sources"
    for source in spec.sources:
        _clone_exact(source, sources / source.component)
    python = _ensure_venv(runtime_root)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            str(sources / "vera-mono"),
            "-e",
            str(sources / "portal"),
            "-e",
            str(sources / "pre-active"),
            "-e",
            str(sources / "volition"),
        ]
    )
    return python


def _verify_imports(python: Path) -> dict[str, str]:
    code = (
        "import json,portal,pre_active,volition,vera_core;"
        "print(json.dumps({"
        "'portal':portal.__file__,"
        "'pre_active':pre_active.__file__,"
        "'volition':volition.__file__,"
        "'vera_core':vera_core.__file__"
        "}))"
    )
    completed = _run([str(python), "-c", code], capture=True)
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("import verification did not return an object")
    return {str(key): str(path) for key, path in value.items()}


def _pid_alive(pid: int) -> bool:
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"if(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue){{exit 0}}else{{exit 1}}",
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _host_running(runtime_root: Path) -> bool:
    heartbeat = runtime_root / "bridge" / "heartbeat.json"
    if not heartbeat.is_file():
        return False
    try:
        payload = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
        pid = int(payload["pid"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    return _pid_alive(pid)


def _start_host(runtime_root: Path, python: Path) -> subprocess.Popen[bytes] | None:
    if _host_running(runtime_root):
        return None
    logs = runtime_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = open(logs / "runtime.stdout.log", "ab", buffering=0)
    stderr = open(logs / "runtime.stderr.log", "ab", buffering=0)
    env = dict(os.environ)
    env["VERA_RUNTIME_ROOT"] = str(runtime_root)
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    try:
        return subprocess.Popen(
            [str(python), "-m", "portal.desktop_host"],
            cwd=str(runtime_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()


def _qualify(runtime_root: Path, python: Path) -> dict[str, object]:
    env = dict(os.environ)
    env["VERA_RUNTIME_ROOT"] = str(runtime_root)
    completed = _run(
        [
            str(python),
            "-m",
            "portal.desktop_qualify",
            "--runtime-root",
            str(runtime_root),
            "--timeout-seconds",
            "180",
        ],
        env=env,
        capture=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or payload.get("qualified") is not True:
        raise RuntimeError("runtime qualification did not report qualified=true")
    return payload


def install(
    *,
    runtime_root: Path,
    spec: InstallSpec,
    activate: bool,
) -> dict[str, object]:
    runtime_root = prepare_stage_root(runtime_root, spec)
    spec.write(runtime_root / "RUNTIME_INSTALL_SPEC.json")

    python = _install_sources(runtime_root, spec)
    imports = _verify_imports(python)
    _start_host(runtime_root, python)
    qualification = _qualify(runtime_root, python)

    host_launcher = runtime_root / "StartVeraRuntime.cmd"
    host_launcher.write_text(
        launcher_text(runtime_root, python, "portal.desktop_host"),
        encoding="ascii",
    )
    desktop_launcher = runtime_root / "PortalDesktop.cmd"
    desktop_launcher.write_text(
        launcher_text(runtime_root, python, "portal.desktop_app"),
        encoding="ascii",
    )

    task_name = None
    if activate:
        task_name = "VeraDesktopRuntime-" + re.sub(
            r"[^A-Za-z0-9_.-]",
            "_",
            spec.install_id,
        )
        _run(
            build_logon_task_command(
                task_name=task_name,
                launcher=host_launcher,
            )
        )
        query = _run(
            [
                "schtasks.exe",
                "/Query",
                "/TN",
                task_name,
                "/FO",
                "LIST",
                "/V",
            ],
            capture=True,
        )
        if task_name not in query.stdout:
            raise RuntimeError("registered logon task could not be read back")

    result = {
        "schema": "PORTAL_DESKTOP_INSTALLATION_RESULT_V1",
        "runtime_root": str(runtime_root),
        "install_id": spec.install_id,
        "source_map": {
            item.component: {
                "repository": item.repository,
                "ref": item.ref,
                "sha": item.sha,
            }
            for item in spec.sources
        },
        "imports": imports,
        "qualification": qualification,
        "activation": {
            "requested": activate,
            "task_name": task_name,
            "active_at_logon": bool(activate),
        },
        "protected_effect_authority": False,
        "native_openai_router_replaced": False,
        "installed_at": time.time(),
    }
    (runtime_root / "INSTALLATION_RESULT.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _spec_from_args(args: argparse.Namespace) -> InstallSpec:
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id=args.install_id,
        sources=(
            RuntimeSource(
                "vera-mono",
                "thebrazenbeard/vera-mono",
                "main",
                args.vera_mono_sha,
            ),
            RuntimeSource(
                "portal",
                "thebrazenbeard/portal",
                args.portal_ref,
                args.portal_sha,
            ),
            RuntimeSource(
                "pre-active",
                "thebrazenbeard/pre-active",
                "main",
                args.pre_active_sha,
            ),
            RuntimeSource(
                "volition",
                "thebrazenbeard/volition",
                "main",
                args.volition_sha,
            ),
        ),
    )
    spec.validate()
    return spec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--install-id", required=True)
    parser.add_argument("--portal-sha", required=True)
    parser.add_argument(
        "--portal-ref",
        default="work/portal-desktop-vera-runtime-v1",
    )
    parser.add_argument("--vera-mono-sha", required=True)
    parser.add_argument("--pre-active-sha", required=True)
    parser.add_argument("--volition-sha", required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    result = install(
        runtime_root=args.runtime_root,
        spec=_spec_from_args(args),
        activate=args.activate,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
