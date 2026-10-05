from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import portal.cli as portal_cli


ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def run(self, **kwargs):
        self.calls.append(("run", kwargs))
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            generation=1,
            wave_run_id=f"{kwargs['session_id']}::g1",
            packets=(),
            summary={"active": 0, "held": 0, "terminal": 0},
        )

    def run_until_idle(self, **kwargs):
        self.calls.append(("run_until_idle", kwargs))
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            cycles=(),
            stop_reason="WAITING_ACTIVE",
            idle_cycles=1,
            summary={"active": 1, "held": 0, "terminal": 0},
        )

    def continue_run(self, **kwargs):
        self.calls.append(("continue", kwargs))
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            generation=2,
            wave_run_id=f"{kwargs['session_id']}::g2",
            packets=(),
            summary={"active": 1, "held": 0, "terminal": 0},
        )

    def hold(self, **kwargs):
        self.calls.append(("hold", kwargs))
        return {
            "session_id": kwargs["session_id"],
            "control_state": "RUNNING",
            "generation": 2,
            "summary": {"active": 1, "held": 0, "terminal": 0},
            "subjects": [],
        }

    def complete(self, **kwargs):
        self.calls.append(("complete", kwargs))
        return {
            "session_id": kwargs["session_id"],
            "control_state": "RUNNING",
            "generation": 2,
            "summary": {"active": 0, "held": 0, "terminal": 1},
            "subjects": [],
        }

    def stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        return {
            "session_id": kwargs["session_id"],
            "control_state": "STOPPED",
            "generation": 2,
            "summary": {"active": 1, "held": 0, "terminal": 0},
            "subjects": [],
        }

    def status(self, session_id: str):
        self.calls.append(("status", {"session_id": session_id}))
        return {
            "session_id": session_id,
            "control_state": "RUNNING",
            "generation": 2,
            "summary": {"active": 1, "held": 0, "terminal": 0},
            "subjects": [],
        }


def test_session_run_mode_uses_command_session(tmp_path, monkeypatch, capsys) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--max-parallel", "4",
        "--occupied-node", "lappy=54",
        "--occupied-node", "worklaptop=0",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"
    assert payload["session_id"] == "portfolio"
    name, kwargs = FakeSession.calls[0]
    assert name == "run_until_idle"
    assert kwargs["occupied_node_slots"] == {"lappy": 54, "worklaptop": 0}
    assert kwargs["budget"].max_parallel == 4


def test_session_continue_hold_status_and_stop_are_top_level_commands(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    common = ["--state-db", str(tmp_path / "portal.sqlite3"), "--session-id", "portfolio"]

    code = portal_cli.entrypoint([
        "continue",
        *common,
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_CONTINUE_V1"

    code = portal_cli.entrypoint([
        "hold", *common, "--subject-id", "project-runner",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_HOLD_V1"

    code = portal_cli.entrypoint(["status", *common])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_STATUS_V1"

    code = portal_cli.entrypoint(["stop", *common])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_STOP_V1"


def test_session_complete_routes_through_verification(tmp_path, monkeypatch, capsys) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "complete",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--session-id", "portfolio",
        "--subject-id", "project-runner",
        "--verifier", "vera-review",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_COMPLETE_V1"
    name, kwargs = FakeSession.calls[0]
    assert name == "complete"
    assert kwargs["verifier"] == "vera-review"

def test_session_run_with_worker_backends_builds_execution_adapter(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    built: dict[str, object] = {}
    sentinel_adapter = object()

    class FakeProcessDriver:
        def __init__(self, **kwargs):
            built["driver_kwargs"] = kwargs

    def fake_build(driver):
        built["driver"] = driver
        return sentinel_adapter

    monkeypatch.setattr(
        portal_cli,
        "PortalProposalProcessAdapter",
        FakeProcessDriver,
        raising=False,
    )
    monkeypatch.setattr(
        portal_cli,
        "build_process_proposal_execution_adapter",
        fake_build,
        raising=False,
    )
    monkeypatch.setattr(
        portal_cli,
        "load_worker_backends",
        lambda path: {"worklaptop": object()},
    )

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--worker-backends", str(tmp_path / "workers.yaml"),
        "--workspace-root", str(tmp_path / "workspaces"),
        "--worker-holder-prefix", "portal-host",
        "--delivery-lease-ttl", "123",
        "--once",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"
    name, kwargs = FakeSession.calls[0]
    assert name == "run"
    assert kwargs["execution_adapter"] is sentinel_adapter
    driver_kwargs = built["driver_kwargs"]
    assert driver_kwargs["workspace_root"] == tmp_path / "workspaces"
    assert driver_kwargs["holder_prefix"] == "portal-host"
    assert driver_kwargs["delivery_lease_ttl"] == 123.0


def test_session_continue_with_worker_backends_passes_execution_adapter(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    sentinel_adapter = object()

    class FakeProcessDriver:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(
        portal_cli,
        "PortalProposalProcessAdapter",
        FakeProcessDriver,
        raising=False,
    )
    monkeypatch.setattr(
        portal_cli,
        "build_process_proposal_execution_adapter",
        lambda driver: sentinel_adapter,
        raising=False,
    )
    monkeypatch.setattr(
        portal_cli,
        "load_worker_backends",
        lambda path: {"worklaptop": object()},
    )

    code = portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--worker-backends", str(tmp_path / "workers.yaml"),
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_CONTINUE_V1"
    name, kwargs = FakeSession.calls[0]
    assert name == "continue"
    assert kwargs["execution_adapter"] is sentinel_adapter

