from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from .desktop_install import InstallSpec, RuntimeSource, StageRootConflict, prepare_stage_root


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
        capture_output=True,
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
    if os.name == "nt":
        # Bind source bytes to the exact Git blob, not the user's global
        # autocrlf policy. Some upstream blobs contain mixed newlines while
        # .gitattributes demands LF, which otherwise creates a false dirty
        # checkout before any user or installer modification.
        # These settings are clone-local; they do not alter tracked sources.
        _run(["git", "-C", str(destination), "config", "--local", "core.longpaths", "true"])
        _run(["git", "-C", str(destination), "config", "--local", "core.autocrlf", "false"])
        attributes = destination / ".git" / "info" / "attributes"
        expected_attributes = "* -text\n"
        attributes.parent.mkdir(parents=True, exist_ok=True)
        if attributes.exists():
            if attributes.read_text(encoding="utf-8") != expected_attributes:
                raise RuntimeError("staged clone contains unexpected local Git attributes")
        else:
            attributes.write_text(expected_attributes, encoding="utf-8")
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
    dirty = _run(
        ["git", "-C", str(destination), "status", "--porcelain", "--untracked-files=no"],
        capture=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            f"{source.component} tracked source checkout is not clean after exact checkout: {dirty}"
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


def _verify_imports(python: Path, runtime_root: Path) -> dict[str, str]:
    code = (
        "import json,portal,pre_active,volition,vera_core;"
        "print(json.dumps({"
        "'portal':portal.__file__,"
        "'pre_active':pre_active.__file__,"
        "'volition':volition.__file__,"
        "'vera_core':vera_core.__file__"
        "}))"
    )
    completed = _run(
        [str(python), "-c", code],
        cwd=runtime_root,
        capture=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("import verification did not return an object")
    return {str(key): str(path) for key, path in value.items()}


def host_shim_text(runtime_root: Path) -> str:
    return (
        "import os\n"
        f"os.environ['VERA_RUNTIME_ROOT'] = {str(runtime_root)!r}\n"
        "from portal.desktop_host import main\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _check_components(python: Path, runtime_root: Path) -> dict[str, object]:
    completed = _run(
        [str(python), "-m", "portal.desktop_host", "--runtime-root", str(runtime_root), "--check"],
        cwd=runtime_root,
        capture=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or payload.get("components_loaded") is not True:
        raise RuntimeError("resident component check did not report components_loaded=true")
    return payload


def _verify_existing_binding(python: Path, runtime_root: Path) -> None:
    # Execute in the installation's interpreter so import-origin checks bind
    # the editable installed packages, rather than this bootstrap checkout.
    _run([
        str(python), "-c",
        "from pathlib import Path; import sys; from portal.desktop_host import load_manifest; load_manifest(Path(sys.argv[1]))",
        str(runtime_root),
    ], cwd=runtime_root, capture=True)


def _check_existing_runtime(runtime_root: Path, python: Path, spec: InstallSpec) -> None:
    from .desktop_ipc import FileBridgeClient
    from .desktop_supervisor import RuntimeState, RuntimeSupervisor, RuntimeSupervisorConfig

    supervisor = RuntimeSupervisor(RuntimeSupervisorConfig(runtime_root))
    status = supervisor.status()
    if status.state is RuntimeState.OFFLINE:
        _check_components(python, runtime_root)
        _start_host(runtime_root, python)
        return
    if (
        status.components_loaded is not True
        or status.heartbeat_age_seconds is None
        or status.heartbeat_age_seconds > supervisor.config.heartbeat_ttl_seconds
        or (status.state is not RuntimeState.ACTIVE and not (
            status.state is RuntimeState.BLOCKED and status.reason == "no_admissible_cognition_route"
        ))
    ):
        raise RuntimeError(f"runtime is not healthy enough for logon activation: {status.reason}")
    payload = FileBridgeClient(runtime_root, timeout_seconds=5.0).request("desktop_status")
    manifest = payload.get("source_manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("sources") != spec.to_mapping()["sources"]
        or payload.get("protected_effect_authority") is not False
    ):
        raise RuntimeError("running runtime does not match the exact installation source bindings")


def _launch_logged(runtime_root: Path, python: Path, script: Path) -> int:
    logs = runtime_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = open(logs / "runtime.stdout.log", "ab", buffering=0)
    stderr = open(logs / "runtime.stderr.log", "ab", buffering=0)
    env = dict(os.environ)
    env["VERA_RUNTIME_ROOT"] = str(runtime_root)
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    ) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [str(python), str(script)],
            cwd=str(runtime_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
        )
        return int(process.pid)
    finally:
        stdout.close()
        stderr.close()


def _start_host(runtime_root: Path, python: Path) -> None:
    from .desktop_supervisor import RuntimeState, RuntimeSupervisor, RuntimeSupervisorConfig

    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(runtime_root),
        launcher=lambda exe, script: _launch_logged(runtime_root, exe, script),
    )
    status = supervisor.ensure_started()
    deadline = time.monotonic() + 30.0
    while status.state is RuntimeState.STARTING and time.monotonic() < deadline:
        time.sleep(0.1)
        status = supervisor.status()
    if (
        status.components_loaded is not True
        or status.heartbeat_age_seconds is None
        or status.heartbeat_age_seconds > supervisor.config.heartbeat_ttl_seconds
        or status.state in (RuntimeState.OFFLINE, RuntimeState.STARTING, RuntimeState.DEGRADED)
        or (status.state is RuntimeState.BLOCKED and status.reason != "no_admissible_cognition_route")
    ):
        raise RuntimeError(f"runtime startup failed: {status.reason}; inspect logs/runtime.stderr.log")


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


def _record_activation_state(
    runtime_root: Path,
    *,
    requested: bool,
    qualified: bool,
    active: bool,
    local_cognition_qualified: bool = True,
) -> None:
    path = runtime_root / "RUNTIME_INSTALL_SPEC.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("runtime install spec is not an object")
    payload["activation"] = {
        "requested": requested,
        "qualified": qualified,
        "active": active,
    }
    payload["claim_ceiling"] = ("" if local_cognition_qualified else "COMPONENT_CHECKED_COGNITION_NOT_PROVEN_") + (
        "QUALIFIED_USER_LOCAL_RUNTIME_ACTIVE_AT_LOGON_"
        "NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY"
        if active
        else
        "QUALIFIED_STAGED_USER_LOCAL_RUNTIME_NOT_REGISTERED_FOR_LOGON_"
        "NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY"
    )
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def install(
    *,
    runtime_root: Path,
    spec: InstallSpec,
    activate: bool,
    dry_run: bool = False,
    allow_local_no_paid_compute: bool = False,
) -> dict[str, object]:
    spec.validate()
    runtime_root = Path(runtime_root).resolve()
    user_root = Path.home().resolve()
    if runtime_root == user_root or not runtime_root.is_relative_to(user_root):
        raise ValueError("runtime root must be a dedicated directory inside the current user's home")
    policy = {"allow_local_no_paid_compute": bool(allow_local_no_paid_compute)}
    prior_path = runtime_root / "RUNTIME_INSTALL_SPEC.json"
    prior: dict[str, object] | None = None
    if prior_path.exists():
        if InstallSpec.read(prior_path) != spec:
            raise StageRootConflict("stage root is already bound to a different exact source spec; choose a new root")
        prior = json.loads(prior_path.read_text(encoding="utf-8-sig"))
        if prior.get("cognition_policy", {"allow_local_no_paid_compute": False}) != policy:
            raise StageRootConflict("stage root is already bound to a different cognition policy; choose a new root")
    source_map = {
        item.component: {"repository": item.repository, "ref": item.ref, "sha": item.sha}
        for item in spec.sources
    }
    if dry_run:
        return {
            "schema": "PORTAL_DESKTOP_INSTALLATION_PLAN_V1", "dry_run": True,
            "runtime_root": str(runtime_root), "install_id": spec.install_id,
            "source_map": source_map, "cognition_policy": policy,
            "activation_requested": activate, "protected_effect_authority": False,
        }

    activation = prior.get("activation", {}) if prior else {}
    result_path = runtime_root / "INSTALLATION_RESULT.json"
    qualified_prior = isinstance(activation, dict) and activation.get("qualified") is True
    if qualified_prior:
        if not result_path.is_file():
            raise StageRootConflict("qualified runtime lacks its installation result; restore evidence or choose a new root")
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict) or not isinstance(result.get("qualification"), dict) or result["qualification"].get("qualified") is not True:
            raise StageRootConflict("qualified runtime has invalid installation evidence; choose a new root")
        _verify_existing_binding(_venv_python(runtime_root), runtime_root)
        # Re-running an exact installation returns its retained evidence. It
        # must never checkout sources, reinstall packages, or reset activation
        # while those editable sources may be executing in a resident process.
        if not activate or activation.get("active") is True:
            return {**result, "reused_installation": True, "qualification_rechecked": False}
    else:
        from .desktop_supervisor import RuntimeState, RuntimeSupervisor, RuntimeSupervisorConfig
        observed = RuntimeSupervisor(RuntimeSupervisorConfig(runtime_root)).status()
        if observed.state is not RuntimeState.OFFLINE:
            raise StageRootConflict("unqualified root may have a resident process; inspect health and stage a separate root before installation")

    runtime_root = prepare_stage_root(runtime_root, spec)
    log = runtime_root / "logs/install.log"
    try:
        if not qualified_prior:
            payload = json.loads(prior_path.read_text(encoding="utf-8-sig"))
            payload["cognition_policy"] = policy
            _write_json(prior_path, payload)
            python = _install_sources(runtime_root, spec)
            imports = _verify_imports(python, runtime_root)
            qualification = _check_components(python, runtime_root)
            (runtime_root / "host/vera_unified_host.py").write_text(host_shim_text(runtime_root), encoding="utf-8")
            (runtime_root / "StartVeraRuntime.cmd").write_text(launcher_text(runtime_root, python, "portal.desktop_supervisor"), encoding="utf-8")
            (runtime_root / "PortalDesktop.cmd").write_text(launcher_text(runtime_root, python, "portal.desktop_app"), encoding="utf-8")
            _start_host(runtime_root, python)
            if allow_local_no_paid_compute:
                qualification = _qualify(runtime_root, python)
            qualification = {**qualification, "qualified": True, "local_cognition_qualified": bool(allow_local_no_paid_compute)}
        else:
            imports = result.get("imports", {})
            qualification = result["qualification"]
            _check_existing_runtime(runtime_root, _venv_python(runtime_root), spec)

        task_name = None
        if activate:
            task_name = "VeraDesktopRuntime-" + re.sub(r"[^A-Za-z0-9_.-]", "_", spec.install_id)
            _run(build_logon_task_command(task_name=task_name, launcher=runtime_root / "StartVeraRuntime.cmd"))
            query = _run(["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST", "/V"], capture=True)
            if task_name not in query.stdout:
                raise RuntimeError("registered logon task could not be read back; inspect task state before retry")

        _record_activation_state(runtime_root, requested=activate, qualified=True, active=bool(activate), local_cognition_qualified=bool(qualification.get("local_cognition_qualified")))
        result = {
            "schema": "PORTAL_DESKTOP_INSTALLATION_RESULT_V1",
            "runtime_root": str(runtime_root),
            "install_id": spec.install_id,
            "source_map": source_map,
            "cognition_policy": policy,
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
        _write_json(result_path, result)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()}: installation component checks passed; local cognition proof={allow_local_no_paid_compute}; activation={activate}\n")
        return result
    except Exception as exc:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()}: installation failed; no activation success claimed\n")
            stream.write(traceback.format_exc())
            if isinstance(exc, subprocess.CalledProcessError):
                stream.write(f"\nstdout:\n{exc.stdout or ''}\nstderr:\n{exc.stderr or ''}\n")
            stream.write("Inspect this log and runtime.stderr.log; keep the exact spec and retry the same staged root or select a new root for changed sources/policy.\n")
        raise


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-local-no-paid-compute", action="store_true")
    args = parser.parse_args()
    result = install(
        runtime_root=args.runtime_root,
        spec=_spec_from_args(args),
        activate=args.activate,
        dry_run=args.dry_run,
        allow_local_no_paid_compute=args.allow_local_no_paid_compute,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
