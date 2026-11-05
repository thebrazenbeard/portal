from __future__ import annotations

import argparse
import ctypes
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from .desktop_install import InstallSpec, RuntimeSource, prepare_stage_root
from .runtime_source_attestation import write_attestation, verify_attestation


_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_WINDOWS_SID_RE = re.compile(r"^S-1-[0-9]+(?:-[0-9]+)+$", re.IGNORECASE)
_TASK_XML_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
_UNSAFE_CMD_PATH_CHARS = frozenset('%!^"&|<>\r\n')


def _validated_cmd_path(path: Path) -> str:
    """Fail before writing a CMD launcher whose path would be reinterpreted."""
    value = str(path)
    if not value or any(char in _UNSAFE_CMD_PATH_CHARS or ord(char) < 32 for char in value):
        raise ValueError("Windows CMD launcher path contains unsafe characters")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Windows CMD launcher path must be ASCII") from exc
    return value


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink() or path.is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except (FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _reject_reparse_ancestors(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if _is_link_or_reparse(ancestor):
            raise RuntimeError(
                f"runtime staging path contains a symlink, junction, "
                f"or reparse point: {ancestor}"
            )


def _require_fresh_runtime_leaf(runtime_root: Path) -> None:
    """Never stage over prior evidence or prepositioned filesystem redirects."""
    _reject_reparse_ancestors(runtime_root)
    if not runtime_root.exists():
        return
    if not runtime_root.is_dir():
        raise RuntimeError("runtime stage leaf exists and is not a directory")
    if any(runtime_root.iterdir()):
        raise RuntimeError(
            "runtime stage leaf is not empty; inspect it and use a new staged install leaf"
        )


def launcher_text(runtime_root: Path, python: Path, module: str) -> str:
    root_value = _validated_cmd_path(runtime_root)
    python_value = _validated_cmd_path(python)
    if module not in ("portal.desktop_host", "portal.desktop_app"):
        raise ValueError("unsupported Windows CMD launcher module")
    return (
        "@echo off\r\n"
        f'set "VERA_RUNTIME_ROOT={root_value}"\r\n'
        'set "PYTHONDONTWRITEBYTECODE=1"\r\n'
        f'"{python_value}" -B -m {module}\r\n'
    )


def build_logon_task_command(
    *,
    task_name: str,
    launcher: Path,
) -> list[str]:
    if not _TASK_NAME_RE.fullmatch(task_name):
        raise ValueError("task_name contains unsupported characters")
    launcher_value = _validated_cmd_path(Path(launcher).resolve())
    return [
        "schtasks.exe",
        "/Create",
        "/SC",
        "ONLOGON",
        "/TN",
        task_name,
        "/TR",
        launcher_value,
    ]



def _require_unclaimed_logon_task(task_name: str) -> None:
    """Inspect the complete scheduler inventory; an uncertain answer blocks activation.

    The single-task /Query exit code cannot distinguish 'not found' from
    access denial or scheduler failure, so use a successful complete listing.
    /Create is also issued without /F and with stdin closed to resist races.
    """
    if not _TASK_NAME_RE.fullmatch(task_name):
        raise ValueError("task_name contains unsupported characters")
    try:
        inventory = _run(
            ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
            capture=True,
        )
        rows = list(csv.reader(io.StringIO(inventory.stdout.lstrip("\ufeff")), strict=True))
    except (OSError, subprocess.CalledProcessError, UnicodeError, csv.Error) as exc:
        raise RuntimeError(
            "scheduled-task inventory unavailable; refusing logon activation"
        ) from exc
    if not rows:
        raise RuntimeError("scheduled-task inventory empty; refusing logon activation")
    expected = ("\\" + task_name).casefold()
    for row in rows:
        if len(row) < 3 or not row[0].startswith("\\"):
            raise RuntimeError(
                "scheduled-task inventory malformed; refusing logon activation"
            )
        if row[0].casefold() == expected:
            raise RuntimeError(
                "logon task already exists; refusing to replace it"
            )


def _current_scheduler_account() -> tuple[str, str]:
    """Bind the expected principal to this installer process, before /Create."""
    try:
        output = _run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"], capture=True,
        ).stdout
        rows = list(csv.reader(io.StringIO(output.lstrip("\ufeff")), strict=True))
    except (OSError, subprocess.CalledProcessError, UnicodeError, csv.Error) as exc:
        raise RuntimeError("current scheduler principal unavailable; refusing activation") from exc
    if (len(rows) != 1 or len(rows[0]) != 2 or not rows[0][0].strip()
            or not _WINDOWS_SID_RE.fullmatch(rows[0][1].strip())):
        raise RuntimeError("current scheduler principal malformed; refusing activation")
    return rows[0][0].strip(), rows[0][1].strip()


def _require_exact_logon_task_readback(
    task_name: str, launcher: Path, xml: str, *, account: str, account_sid: str,
) -> None:
    """Fail closed unless scheduler XML matches this exact owned task definition.

    Checking TaskName alone is insufficient after registration: a competing
    actor can preposition a different action/trigger/principal under the same
    name. /Query /TN /XML exposes the actual definition without mutating it.
    """
    failure = "registered logon task definition could not be read back exactly"
    if not _TASK_NAME_RE.fullmatch(task_name) or not isinstance(xml, str) or len(xml) > 262144:
        raise RuntimeError(failure)
    try:
        root = ET.fromstring(xml.lstrip("\ufeff"))
    except ET.ParseError as exc:
        raise RuntimeError(failure) from exc
    n = _TASK_XML_NS
    if root.tag != n + "Task":
        raise RuntimeError(failure)
    uri = root.findtext(f"{n}RegistrationInfo/{n}URI")
    triggers = root.findall(f"{n}Triggers/*")
    actions = root.findall(f"{n}Actions/*")
    principals = root.findall(f"{n}Principals/{n}Principal")
    if (uri is None or uri.casefold() != ("\\" + task_name).casefold()
            or len(triggers) != 1 or triggers[0].tag != n + "LogonTrigger"
            or len(actions) != 1 or actions[0].tag != n + "Exec"
            or len(principals) != 1):
        raise RuntimeError(failure)
    trigger = triggers[0]
    if (trigger.findtext(n + "Enabled", "true").casefold() != "true"
            or root.findtext(f"{n}Settings/{n}Enabled", "true").casefold() != "true"):
        raise RuntimeError(failure)
    # /SC ONLOGON without /RU allows a logon trigger for any local user.
    # If the scheduler binds a specific user, it must still be this account.
    trigger_user = trigger.findtext(n + "UserId")
    allowed_users = {account.casefold(), account_sid.casefold()}
    if trigger_user and trigger_user.casefold() not in allowed_users:
        raise RuntimeError(failure)
    action = actions[0]
    # /TR is an exact launcher, with neither arguments nor a working directory.
    if (action.findtext(n + "Command") != str(Path(launcher).resolve())
            or action.findtext(n + "Arguments") not in (None, "")
            or action.findtext(n + "WorkingDirectory") not in (None, "")):
        raise RuntimeError(failure)
    principal = principals[0]
    user_id = principal.findtext(n + "UserId")
    if (user_id is None or user_id.casefold() not in allowed_users
            or principal.find(n + "GroupId") is not None
            or principal.findtext(n + "RunLevel") != "LeastPrivilege"
            or principal.findtext(n + "LogonType") not in (None, "InteractiveToken")):
        raise RuntimeError(failure)
    context = root.find(n + "Actions")
    context_id = context.get("Context") if context is not None else None
    principal_id = principal.get("id")
    if context_id and context_id != principal_id:
        raise RuntimeError(failure)


def _record_task_registration_uncertain(runtime_root: Path, task_name: str) -> None:
    """Retain a fail-closed recovery marker; never delete or replace a scheduler task."""
    try:
        _record_activation_state(
            runtime_root, requested=True, qualified=True, active=False,
        )
    except (OSError, ValueError, RuntimeError):
        # A locked or malformed runtime spec cannot be trusted; the separate
        # marker still identifies the task requiring manual reconciliation.
        pass
    marker = {
        "schema": "PORTAL_LOGON_TASK_REGISTRATION_UNCERTAIN_V1",
        "task_name": task_name,
        "state": "UNCERTAIN_REQUIRES_MANUAL_RECONCILIATION",
        "active": False,
        "recorded_at": time.time(),
    }
    with (runtime_root / "TASK_REGISTRATION_UNCERTAIN.json").open(
        "x", encoding="utf-8",
    ) as sink:
        json.dump(marker, sink, sort_keys=True, indent=2)
        sink.write("\n")


def _trusted_windows_scheduler_tool(name: str) -> str:
    """Resolve scheduler commands using the OS system directory, not PATH.

    A shadowed schtasks/whoami binary could forge *both* the inventory and
    post-create receipt. GetSystemDirectoryW bypasses the mutable PATH and
    SystemRoot environment variables; any uncertainty blocks activation.
    """
    name = name.casefold()
    if os.name != "nt" or name not in {"schtasks.exe", "whoami.exe"}:
        raise RuntimeError("trusted Windows scheduler tool unavailable")
    try:
        system_dir = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetSystemDirectoryW(system_dir, len(system_dir))
        if not 0 < size < len(system_dir):
            raise RuntimeError("Windows system directory cannot be resolved")
        binary = Path(system_dir.value) / name
        if not binary.is_file():
            raise RuntimeError("trusted Windows scheduler tool is missing")
        return str(binary)
    except (AttributeError, OSError, ValueError) as exc:
        raise RuntimeError("trusted Windows scheduler tool unavailable") from exc


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    noninteractive: bool = False,
) -> subprocess.CompletedProcess[str]:
    if args and args[0].casefold() in {"schtasks.exe", "whoami.exe"}:
        # Centralize pinning, including inventory, registration and readback.
        args = [_trusted_windows_scheduler_tool(args[0]), *args[1:]]
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
        stdin=subprocess.DEVNULL if noninteractive else None,
    )


def _clone_exact(source: RuntimeSource, destination: Path) -> None:
    source.validate()
    _reject_reparse_ancestors(destination)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(
            f"{source.component} source destination already exists; "
            "use a new staged install leaf"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
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
            "-c",
            "core.autocrlf=false",
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
    status = _run(
        [
            "git", "-C", str(destination), "status", "--porcelain=v1",
            "--untracked-files=all", "--ignored=matching",
        ],
        capture=True,
    ).stdout
    if status.strip():
        raise RuntimeError(
            f"{source.component} source checkout contains modified, "
            "untracked, or ignored files; refusing editable install"
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


def _assert_fresh_component_destinations(runtime_root: Path, spec: InstallSpec) -> None:
    spec.validate()
    sources = runtime_root / "sources"
    _reject_reparse_ancestors(sources)
    if not sources.resolve().is_relative_to(runtime_root.resolve()):
        raise RuntimeError("source staging directory escapes the chosen runtime root")
    for source in spec.sources:
        destination = sources / source.component
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(
                f"{source.component} source destination already exists; "
                "use a new staged install leaf"
            )


def _install_sources(runtime_root: Path, spec: InstallSpec) -> Path:
    # Check the entire set before a single clone has effects.
    _assert_fresh_component_destinations(runtime_root, spec)
    sources = runtime_root / "sources"
    for source in spec.sources:
        _clone_exact(source, sources / source.component)
    python = _ensure_venv(runtime_root)
    _run(
        [
            str(python),
            "-B",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
        ] + [
            arg
            for source in spec.sources
            for arg in ("-e", str(sources / source.component))
        ],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return python


def _verify_imports(python: Path, runtime_root: Path) -> dict[str, str]:
    installed = InstallSpec.read(runtime_root / "RUNTIME_INSTALL_SPEC.json")
    module_sources = {
        "portal": "portal",
        "pre_active": "pre-active",
        "volition": "volition",
        "vera_core": "vera-mono",
    }
    if installed.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2":
        module_sources["meso_crct"] = "meso-crct"
    modules = list(module_sources)
    code = (
        "import json;"
        + "".join(f"import {name};" for name in modules)
        + "print(json.dumps({"
        + ",".join(f"'{name}':{name}.__file__" for name in modules)
        + "}))"
    )
    completed = _run(
        [str(python), "-B", "-c", code],
        cwd=runtime_root,
        capture=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or set(value) != set(module_sources):
        raise RuntimeError("import verification returned missing or unexpected modules")
    verified: dict[str, str] = {}
    for module, component in module_sources.items():
        declared = value[module]
        if not isinstance(declared, str) or not Path(declared).is_absolute():
            raise RuntimeError(f"import path is not absolute: {module}")
        try:
            staged_source = runtime_root / "sources" / component
            _reject_reparse_ancestors(staged_source)
            source_root = staged_source.resolve(strict=True)
            module_path = Path(declared).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"import path cannot be verified: {module}") from exc
        if not module_path.is_file() or not module_path.is_relative_to(source_root):
            raise RuntimeError(f"import escaped exact staged source: {module}")
        verified[module] = str(module_path)
    return verified


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
            [str(python), "-B", "-m", "portal.desktop_host"],
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


def _terminate_unqualified_host(process: subprocess.Popen[bytes]) -> None:
    """Stop only the child this installer launched, preserving staged evidence."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _qualify(runtime_root: Path, python: Path) -> dict[str, object]:
    env = dict(os.environ)
    env["VERA_RUNTIME_ROOT"] = str(runtime_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = _run(
        [
            str(python),
            "-B",
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
    payload["claim_ceiling"] = (
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
) -> dict[str, object]:
    # Preflight the eventual Windows command files before cloning, installing,
    # or starting a host; normalized absolute paths survive an ONLOGON cwd.
    _require_fresh_runtime_leaf(Path(runtime_root))
    runtime_root = Path(runtime_root).resolve()
    launcher_text(runtime_root, _venv_python(runtime_root), "portal.desktop_host")
    launcher_text(runtime_root, _venv_python(runtime_root), "portal.desktop_app")
    spec.validate()
    _assert_fresh_component_destinations(runtime_root, spec)
    runtime_root = prepare_stage_root(runtime_root, spec)
    spec.write(runtime_root / "RUNTIME_INSTALL_SPEC.json")

    python = _install_sources(runtime_root, spec)
    imports = _verify_imports(python, runtime_root)
    source_attestation = write_attestation(runtime_root, spec)
    staged_host = _start_host(runtime_root, python)
    if staged_host is None:
        raise RuntimeError("staged host was not started by this installer")
    try:
        qualification = _qualify(runtime_root, python)
        verify_attestation(runtime_root, spec)
    except BaseException:
        _terminate_unqualified_host(staged_host)
        raise

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
    task_registration_attempted = False
    try:
        if activate:
            task_name = "VeraDesktopRuntime-" + re.sub(
                r"[^A-Za-z0-9_.-]",
                "_",
                spec.install_id,
            )
            _require_unclaimed_logon_task(task_name)
            account, account_sid = _current_scheduler_account()
            # Mark uncertainty before invoking an effect: a scheduler failure
            # could mean the task was created before the client received error.
            task_registration_attempted = True
            _run(
                build_logon_task_command(
                    task_name=task_name,
                    launcher=host_launcher,
                ),
                noninteractive=True,
            )
            query = _run(
                [
                    "schtasks.exe",
                    "/Query",
                    "/TN",
                    task_name,
                    "/XML",
                ],
                capture=True,
            )
            _require_exact_logon_task_readback(
                task_name, host_launcher, query.stdout,
                account=account, account_sid=account_sid,
            )

        _record_activation_state(
            runtime_root,
            requested=activate,
            qualified=True,
            active=bool(activate),
        )

        result = {
            "schema": (
                "PORTAL_DESKTOP_INSTALLATION_RESULT_V2"
                if spec.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"
                else "PORTAL_DESKTOP_INSTALLATION_RESULT_V1"
            ),
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
            "source_attestation": {
                "schema": source_attestation["schema"],
                "sha256": source_attestation["sha256"],
                "path": "RUNTIME_SOURCE_ATTESTATION.json",
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
    except BaseException:
        # After any attempted registration the task's ownership/status is
        # indeterminate, even when the scheduler operation reported success.
        # Preserve the existing scheduler task and never claim activation.
        marker_error = None
        try:
            if task_registration_attempted:
                _record_task_registration_uncertain(runtime_root, task_name)
        except OSError as exc:
            marker_error = exc
        finally:
            _terminate_unqualified_host(staged_host)
        if marker_error is not None:
            raise RuntimeError(
                "task activation failed and uncertainty marker could not be saved; "
                "manual scheduler reconciliation is required"
            ) from marker_error
        raise


def _spec_from_args(args: argparse.Namespace) -> InstallSpec:
    meso_sha = getattr(args, "meso_sha", None)
    sources = (
        RuntimeSource(
            "vera-mono", "thebrazenbeard/vera-mono", "main", args.vera_mono_sha,
        ),
        RuntimeSource(
            "portal", "thebrazenbeard/portal", args.portal_ref, args.portal_sha,
        ),
        RuntimeSource(
            "pre-active", "thebrazenbeard/pre-active", "main", args.pre_active_sha,
        ),
        RuntimeSource(
            "volition", "thebrazenbeard/volition", "main", args.volition_sha,
        ),
    )
    if meso_sha is not None:
        sources += (RuntimeSource(
            "meso-crct", "thebrazenbeard/meso-crct", "main", meso_sha,
        ),)
    spec = InstallSpec(
        schema=(
            "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2"
            if meso_sha is not None else "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1"
        ),
        install_id=args.install_id,
        sources=sources,
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
        default="main",
    )
    parser.add_argument("--vera-mono-sha", required=True)
    parser.add_argument("--pre-active-sha", required=True)
    parser.add_argument("--volition-sha", required=True)
    parser.add_argument(
        "--meso-sha",
        help="Opt into V2 staged install with a fifth exact MESO-CRCT source SHA",
    )
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
