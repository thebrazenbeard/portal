import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows task launch")


def _terminal_receipt(tasks_root, task_id):
    path = tasks_root / "history" / f"{task_id}.json"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"No terminal receipt for fixture task {task_id}")


def test_installed_native_launcher_preserves_exit_code_outside_checkout(tmp_path):
    elsewhere = tmp_path / "working directory with spaces"
    elsewhere.mkdir()
    tasks = tmp_path / "task state with spaces"
    result = subprocess.run([
        sys.executable, "-m", "runner.cli", "task-start",
        "--name", "native fixture", "--owner", "regression-test",
        "--working-directory", str(elsewhere), "--tasks-root", str(tasks),
        "--command", "exit /b 7",
    ], cwd=elsewhere, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads(result.stdout)
    terminal = _terminal_receipt(tasks, launch["task_id"])
    assert terminal["state"] == "FAILED"
    assert terminal["exit_code"] == 7
    assert not (tasks / "active" / f"{launch['task_id']}.json").exists()


def test_powershell_launcher_uses_active_environment_and_quotes_request_path(tmp_path):
    elsewhere = tmp_path / "PowerShell working directory"
    elsewhere.mkdir()
    tasks = tmp_path / "PowerShell task state"
    script_path = ROOT / "scripts" / "Start-ProjectRunnerTask.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    source = (
        "$ErrorActionPreference='Stop'; "
        "function py { throw 'launcher bypassed the active Python interpreter' }; "
        f"& {quote(script_path)} -Name 'PowerShell fixture' "
        f"-Command {quote('Write-Output \'quoted task text\'; exit 7')} "
        f"-WorkingDirectory {quote(elsewhere)} -TasksRoot {quote(tasks)}"
    )
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if Path(sys.executable).parent.name.lower() == "scripts":
        env["VIRTUAL_ENV"] = str(Path(sys.executable).parent.parent)
    else:
        env.pop("VIRTUAL_ENV", None)
    result = subprocess.run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-EncodedCommand", base64.b64encode(source.encode("utf-16le")).decode("ascii"),
    ], cwd=elsewhere, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads(result.stdout)
    terminal = _terminal_receipt(tasks, launch["task_id"])
    assert terminal["shell"] == "powershell"
    assert terminal["state"] == "FAILED"
    assert terminal["exit_code"] == 7
    assert Path(terminal["stdout_log"]).read_text(encoding="utf-8").strip() == "quoted task text"
    assert not list((tasks / "requests").glob("*.json"))
