from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import portal.cli as portal_cli


ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    calls: list[tuple[str, dict[str, object]]] = []
    resume_spec: dict[str, object] | None = None

    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def save_resume_spec(self, **kwargs):
        self.calls.append(("save_resume_spec", kwargs))

    def load_resume_spec(self, **kwargs):
        self.calls.append(("load_resume_spec", kwargs))
        if self.resume_spec is None:
            raise ValueError("Portal session resume spec does not exist")
        return dict(self.resume_spec)

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

def test_session_run_refreshes_project_runner_task_currentness_each_generation(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    created: list[tuple[str, Path]] = []

    class FakeCurrentness:
        def __init__(self, *, node_id, tasks_root):
            self.node_id = node_id
            self.tasks_root = Path(tasks_root)
            created.append((node_id, self.tasks_root))

        def __call__(self):
            return {self.node_id: 7}

    monkeypatch.setattr(
        portal_cli,
        "LocalProjectRunnerTaskCurrentness",
        FakeCurrentness,
        raising=False,
    )

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--occupied-node", "worklaptop=1",
        "--project-runner-tasks", f"lappy={tmp_path / 'lappy-tasks'}",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"
    assert created == [("lappy", tmp_path / "lappy-tasks")]
    name, kwargs = FakeSession.calls[0]
    assert name == "run_until_idle"
    assert kwargs["occupied_node_slots"] is None
    provider = kwargs["node_occupancy_provider"]
    assert provider() == {"lappy": 7, "worklaptop": 1}


def test_session_continue_uses_fresh_task_currentness_snapshot(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    calls = {"snapshots": 0}

    class FakeCurrentness:
        def __init__(self, *, node_id, tasks_root):
            self.node_id = node_id

        def __call__(self):
            calls["snapshots"] += 1
            return {self.node_id: 5}

    monkeypatch.setattr(
        portal_cli,
        "LocalProjectRunnerTaskCurrentness",
        FakeCurrentness,
        raising=False,
    )

    code = portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--project-runner-tasks", f"lappy={tmp_path / 'lappy-tasks'}",
        "--verifier", "vera-review",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_CONTINUE_V1"
    assert calls["snapshots"] == 1
    name, kwargs = FakeSession.calls[0]
    assert name == "continue"
    assert kwargs["occupied_node_slots"] == {"lappy": 5}
    assert kwargs["verifier"] == "vera-review"


def test_duplicate_occupancy_source_for_node_fails_closed(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--occupied-node", "lappy=1",
        "--project-runner-tasks", f"lappy={tmp_path / 'lappy-tasks'}",
    ])

    assert code == 2
    assert "multiple occupancy sources for node: lappy" in capsys.readouterr().err
    assert FakeSession.calls == []

def test_session_run_defaults_to_resident_refill_polling(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
    ])

    assert code == 0
    capsys.readouterr()
    name, kwargs = FakeSession.calls[0]
    assert name == "run_until_idle"
    assert kwargs["max_cycles"] == 0
    assert kwargs["max_idle_cycles"] == 0
    assert kwargs["poll_seconds"] == 5.0

def test_session_run_persists_non_secret_resume_envelope(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    nodes_path = ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"
    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(nodes_path),
        "--max-parallel", "4",
        "--max-per-identity", "3",
        "--max-per-family", "2",
        "--max-per-lane", "1",
        "--verifier", "vera-review",
        "--host-bridge",
        "--host-node-occupancy",
        "--host-frontier-currentness",
        "--project-runner-tasks", f"lappy={tmp_path / 'lappy-tasks'}",
        "--occupied-node", "worklaptop=0",
        "--once",
    ])

    assert code == 0
    capsys.readouterr()
    save_calls = [
        kwargs
        for name, kwargs in FakeSession.calls
        if name == "save_resume_spec"
    ]
    assert len(save_calls) == 1
    assert save_calls[0]["session_id"] == "portfolio"
    assert save_calls[0]["holder"] == "vera"
    spec = save_calls[0]["spec"]
    assert spec["schema"] == "PORTAL_COMMAND_SESSION_RESUME_V1"
    assert spec["nodes"] == str(nodes_path)
    assert spec["max_parallel"] == 4
    assert spec["max_per_identity"] == 3
    assert spec["max_per_family"] == 2
    assert spec["max_per_lane"] == 1
    assert spec["verifier"] == "vera-review"
    assert spec["host_bridge"] is True
    assert spec["host_node_occupancy"] is True
    assert spec["host_frontier_currentness"] is True
    assert spec["project_runner_tasks"] == [
        f"lappy={tmp_path / 'lappy-tasks'}"
    ]
    assert spec["occupied_nodes"] == ["worklaptop=0"]
    assert "token" not in spec
    assert "github_token" not in spec

def test_session_continue_resume_reconstructs_operational_inputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    nodes_path = ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"
    FakeSession.resume_spec = {
        "schema": "PORTAL_COMMAND_SESSION_RESUME_V1",
        "holder": "vera",
        "wave": str(ROOT / "portfolio" / "advancement_wave.public.json"),
        "corpus": str(ROOT / "portfolio" / "corpus.public.json"),
        "projects": str(ROOT / "registry" / "projects.yaml"),
        "discover_owner": None,
        "write_live_registry": None,
        "static_projects": True,
        "nodes": str(nodes_path),
        "lease_ttl": 321.0,
        "max_parallel": 4,
        "max_per_identity": 3,
        "max_per_family": 2,
        "max_per_lane": 1,
        "verifier": "vera-review",
        "host_bridge": False,
        "worker_backends": None,
        "workspace_root": str(tmp_path / "workers"),
        "worker_holder_prefix": "portal-host",
        "delivery_lease_ttl": 123.0,
        "occupied_nodes": ["worklaptop=0"],
        "project_runner_tasks": [],
        "host_node_occupancy": False,
        "host_frontier_currentness": False,
    }
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    try:
        code = portal_cli.entrypoint([
            "continue",
            "--state-db", str(tmp_path / "portal.sqlite3"),
            "--session-id", "portfolio",
            "--resume",
        ])
    finally:
        FakeSession.resume_spec = None

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == (
        "PORTAL_COMMAND_SESSION_CONTINUE_V1"
    )
    assert FakeSession.calls[0][0] == "load_resume_spec"
    name, kwargs = next(
        item for item in FakeSession.calls if item[0] == "continue"
    )
    assert name == "continue"
    assert kwargs["holder"] == "vera"
    assert kwargs["lease_ttl"] == 321.0
    assert kwargs["budget"].max_parallel == 4
    assert kwargs["budget"].max_per_identity == 3
    assert kwargs["budget"].max_per_family == 2
    assert kwargs["budget"].max_per_lane == 1
    assert kwargs["verifier"] == "vera-review"
    assert kwargs["occupied_node_slots"] == {"worklaptop": 0}

