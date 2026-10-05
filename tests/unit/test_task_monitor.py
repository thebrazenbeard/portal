import json
import os
from pathlib import Path

import pytest

import runner.cli as cli_module
import runner.task_monitor as task_monitor_module
from runner.cli import main
from runner.task_supervisor import _windows_command_argv


def test_task_register_and_status_round_trip(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"

    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "model-training-lane-b",
        "--pid", str(os.getpid()),
        "--owner", "chatgpt",
        "--repository", "thebrazenbeard/vera_model_training",
        "--lane", "lane-b",
        "--command", "python train.py",
    ]) == 0
    registered = json.loads(capsys.readouterr().out)
    task_id = registered["task"]["task_id"]
    assert registered["task"]["name"] == "model-training-lane-b"
    assert registered["task"]["pid"] == os.getpid()

    assert main([
        "task-status",
        "--tasks-root", str(tasks_root),
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "PROJECT_RUNNER_TASK_MONITOR_V1"
    assert status["tasks"] == [{
        **registered["task"],
        "state": "RUNNING",
    }]
    assert (tasks_root / "active" / f"{task_id}.json").exists()


def test_task_commands_share_location_independent_default_root(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks_root = tmp_path / "shared-tasks"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("PROJECT_RUNNER_TASKS_ROOT", str(tasks_root))
    monkeypatch.chdir(elsewhere)

    assert main([
        "task-register",
        "--name", "cwd-independent",
        "--pid", str(os.getpid()),
        "--owner", "test",
    ]) == 0
    registered = json.loads(capsys.readouterr().out)["task"]

    assert main(["task-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"][0]["task_id"] == registered["task_id"]
    assert (tasks_root / "active" / f"{registered['task_id']}.json").exists()


def test_task_start_cli_does_not_depend_on_checkout_cwd(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks_root = tmp_path / "tasks"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("PROJECT_RUNNER_TASKS_ROOT", str(tasks_root))
    monkeypatch.chdir(elsewhere)

    captured = {}

    def fake_launch_background_task(**kwargs):
        captured.update(kwargs)
        return {
            "mode": "PROJECT_RUNNER_TASK_SUPERVISOR_V2",
            "task_id": "a" * 32,
            "supervisor_pid": 1234,
        }

    monkeypatch.setattr(
        cli_module,
        "launch_background_task",
        fake_launch_background_task,
    )

    assert main([
        "task-start",
        "--name", "from-system32",
        "--command", "exit 0",
        "--working-directory", str(tmp_path),
    ]) == 0
    launch = json.loads(capsys.readouterr().out)

    assert launch["task_id"] == "a" * 32
    assert captured["tasks_root"] == tasks_root
    assert captured["working_directory"] == tmp_path
    assert captured["name"] == "from-system32"
    assert captured["command"] == "exit 0"
    assert captured["shell"] == "cmd"


def test_windows_shell_argv_is_explicit_and_bounded():
    cmd = _windows_command_argv("echo hello", "cmd")
    assert Path(cmd[0]).name.lower() == "cmd.exe"
    assert cmd[1:4] == ["/d", "/s", "/c"]
    assert cmd[4] == "echo hello"

    powershell = _windows_command_argv("Write-Output hello", "powershell")
    assert Path(powershell[0]).name.lower() == "powershell.exe"
    assert "-NoProfile" in powershell
    assert "-NonInteractive" in powershell
    assert "-EncodedCommand" in powershell

    with pytest.raises(ValueError, match="unsupported task shell"):
        _windows_command_argv("echo nope", "unknown")


def test_windows_task_launcher_and_read_only_monitor_are_shipped():
    root = Path(__file__).resolve().parents[2]
    launcher_path = root / "scripts" / "Start-ProjectRunnerTask.ps1"
    supervisor_path = root / "runner" / "task_supervisor.py"
    monitor_path = root / "scripts" / "Watch-ProjectRunnerTasks.ps1"

    assert launcher_path.exists()
    assert supervisor_path.exists()
    assert monitor_path.exists()

    launcher = launcher_path.read_text(encoding="utf-8")
    supervisor = supervisor_path.read_text(encoding="utf-8")
    monitor = monitor_path.read_text(encoding="utf-8")
    assert "Start-Process" in launcher
    assert "runner.task_supervisor" in launcher
    assert "subprocess.Popen" in supervisor
    assert "finalize_task" in supervisor
    assert "child.wait()" in supervisor
    assert "return exit_code" in supervisor
    assert "Get-CimInstance Win32_Process" in monitor
    assert "Stop-Process" not in monitor
    assert "Remove-Item" not in monitor


def test_task_status_marks_missing_pid_orphaned(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"

    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "finished-task",
        "--pid", "2147483646",
        "--owner", "test",
    ]) == 0
    capsys.readouterr()

    assert main(["task-status", "--tasks-root", str(tasks_root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"][0]["state"] == "ORPHANED"


def test_task_register_rejects_nonpositive_pid(tmp_path):
    with pytest.raises(ValueError, match="task pid must be positive"):
        main([
            "task-register",
            "--tasks-root", str(tmp_path / "tasks"),
            "--name", "bad-task",
            "--pid", "0",
        ])


def test_task_finalize_moves_success_to_history(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"

    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "successful-task",
        "--pid", str(os.getpid()),
        "--owner", "test",
    ]) == 0
    registered = json.loads(capsys.readouterr().out)["task"]
    task_id = registered["task_id"]

    assert main([
        "task-finalize",
        "--tasks-root", str(tasks_root),
        "--task-id", task_id,
        "--exit-code", "0",
    ]) == 0
    finalized = json.loads(capsys.readouterr().out)["task"]

    assert finalized["state"] == "COMPLETED"
    assert finalized["exit_code"] == 0
    assert finalized["ended_at_utc"]
    assert not (tasks_root / "active" / f"{task_id}.json").exists()
    assert (tasks_root / "history" / f"{task_id}.json").exists()

    assert main(["task-status", "--tasks-root", str(tasks_root)]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == []

    assert main(["task-history", "--tasks-root", str(tasks_root)]) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["tasks"][0]["state"] == "COMPLETED"


def test_task_finalize_records_nonzero_exit_as_failed(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"

    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "failed-task",
        "--pid", str(os.getpid()),
        "--owner", "test",
    ]) == 0
    task_id = json.loads(capsys.readouterr().out)["task"]["task_id"]

    assert main([
        "task-finalize",
        "--tasks-root", str(tasks_root),
        "--task-id", task_id,
        "--exit-code", "7",
    ]) == 0
    finalized = json.loads(capsys.readouterr().out)["task"]
    assert finalized["state"] == "FAILED"
    assert finalized["exit_code"] == 7


def test_task_reconcile_archives_missing_process_as_unknown_exit(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"

    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "lost-supervisor",
        "--pid", "2147483646",
        "--owner", "test",
    ]) == 0
    task_id = json.loads(capsys.readouterr().out)["task"]["task_id"]

    assert main(["task-reconcile", "--tasks-root", str(tasks_root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reconciled"] == 1

    assert not (tasks_root / "active" / f"{task_id}.json").exists()
    historical = json.loads(
        (tasks_root / "history" / f"{task_id}.json").read_text(encoding="utf-8")
    )
    assert historical["state"] == "UNKNOWN_EXIT"
    assert historical["exit_code"] is None
    assert historical["terminal_reason"] == "PROCESS_GONE_WITHOUT_FINAL_RECEIPT"


def test_task_finalize_rejects_unsafe_task_id(tmp_path):
    with pytest.raises(ValueError, match="invalid task id"):
        main([
            "task-finalize",
            "--tasks-root", str(tmp_path / "tasks"),
            "--task-id", "../escape",
            "--exit-code", "0",
        ])


def test_task_process_identity_rejects_reused_pid(monkeypatch):
    record = {
        "pid": 4242,
        "process_started_at_utc": "2026-10-04T12:00:00+00:00",
    }
    monkeypatch.setattr(
        task_monitor_module,
        "_process_is_running",
        lambda pid: pid == 4242,
    )
    monkeypatch.setattr(
        task_monitor_module,
        "_process_started_at_utc",
        lambda pid: "2026-10-04T12:05:00+00:00",
    )
    assert task_monitor_module._process_matches_record(record) is False


def test_task_status_filters_by_lane(tmp_path, capsys):
    tasks_root = tmp_path / "tasks"
    for name, lane in (("lane-a-task", "lane-a"), ("lane-b-task", "lane-b")):
        assert main([
            "task-register",
            "--tasks-root", str(tasks_root),
            "--name", name,
            "--pid", str(os.getpid()),
            "--owner", "test",
            "--lane", lane,
        ]) == 0
        capsys.readouterr()

    assert main([
        "task-status",
        "--tasks-root", str(tasks_root),
        "--lane", "lane-b",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert [task["name"] for task in status["tasks"]] == ["lane-b-task"]
    assert status["lane_counts"] == {"lane-b": {"RUNNING": 1}}


def test_task_status_marks_reused_pid_distinct_from_missing_process(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks_root = tmp_path / "tasks"
    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "reused-pid",
        "--pid", "4242",
        "--owner", "test",
        "--process-started-at-utc", "2026-10-04T12:00:00+00:00",
    ]) == 0
    capsys.readouterr()

    monkeypatch.setattr(
        task_monitor_module,
        "_process_is_running",
        lambda pid: pid == 4242,
    )
    monkeypatch.setattr(
        task_monitor_module,
        "_process_started_at_utc",
        lambda pid: "2026-10-04T12:05:00+00:00",
    )

    assert main(["task-status", "--tasks-root", str(tasks_root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"][0]["state"] == "PID_REUSED"


def test_unverifiable_process_identity_is_not_reconciled_as_dead(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks_root = tmp_path / "tasks"
    assert main([
        "task-register",
        "--tasks-root", str(tasks_root),
        "--name", "identity-unknown",
        "--pid", "4242",
        "--owner", "test",
        "--process-started-at-utc", "2026-10-04T12:00:00+00:00",
    ]) == 0
    capsys.readouterr()

    monkeypatch.setattr(
        task_monitor_module,
        "_process_is_running",
        lambda pid: pid == 4242,
    )
    monkeypatch.setattr(
        task_monitor_module,
        "_process_started_at_utc",
        lambda pid: None,
    )

    assert main(["task-status", "--tasks-root", str(tasks_root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"][0]["state"] == "IDENTITY_UNVERIFIED"

    assert main(["task-reconcile", "--tasks-root", str(tasks_root)]) == 0
    reconciliation = json.loads(capsys.readouterr().out)
    assert reconciliation["reconciled"] == 0
    assert len(list((tasks_root / "active").glob("*.json"))) == 1
