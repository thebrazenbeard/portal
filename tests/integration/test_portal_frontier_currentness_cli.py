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

    def save_resume_spec(self, **kwargs):
        pass

    def run_until_idle(self, **kwargs):
        self.calls.append(("run_until_idle", kwargs))
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            cycles=(),
            stop_reason="WAITING_ACTIVE",
            idle_cycles=1,
            summary={"active": 0, "held": 0, "terminal": 0},
        )

    def continue_run(self, **kwargs):
        self.calls.append(("continue", kwargs))
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            generation=2,
            wave_run_id=f"{kwargs['session_id']}::g2",
            packets=(),
            summary={"active": 0, "held": 0, "terminal": 0},
        )


def test_host_frontier_advertise_and_status_round_trip(tmp_path, capsys) -> None:
    state_db = tmp_path / "portal.sqlite3"
    wave = ROOT / "portfolio" / "advancement_wave.public.json"

    code = portal_cli.entrypoint([
        "host", "frontier-advertise",
        "--state-db", str(state_db),
        "--wave", str(wave),
        "--subject-id", "project-runner",
        "--ref", "main",
        "--exact-head", "a" * 40,
        "--ttl-seconds", "300",
    ])
    assert code == 0
    advertised = json.loads(capsys.readouterr().out)
    assert advertised["mode"] == "PORTAL_HOST_FRONTIER_ADVERTISE_V1"
    assert advertised["observation"]["subject_id"] == "project-runner"
    assert advertised["observation"]["repository"] == "thebrazenbeard/project-runner"
    assert advertised["observation"]["disposition"] == "CURRENT"
    assert len(advertised["observation"]["frontier_sha256"]) == 64

    code = portal_cli.entrypoint([
        "host", "frontier-status",
        "--state-db", str(state_db),
    ])
    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "PORTAL_HOST_FRONTIER_STATUS_V1"
    assert status["observations"] == [advertised["observation"]]


def test_host_frontier_can_explicitly_hold_subject(tmp_path, capsys) -> None:
    state_db = tmp_path / "portal.sqlite3"
    wave = ROOT / "portfolio" / "advancement_wave.public.json"

    code = portal_cli.entrypoint([
        "host", "frontier-advertise",
        "--state-db", str(state_db),
        "--wave", str(wave),
        "--subject-id", "project-runner",
        "--ref", "main",
        "--exact-head", "a" * 40,
        "--disposition", "HELD",
        "--ttl-seconds", "300",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["observation"]["disposition"] == "HELD"


def test_session_run_can_require_host_frontier_currentness(
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
        "--host-frontier-currentness",
    ])

    assert code == 0
    capsys.readouterr()
    name, kwargs = FakeSession.calls[0]
    assert name == "run_until_idle"
    assert callable(kwargs["frontier_currentness_provider"])


def test_session_continue_can_require_host_frontier_currentness(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--host-frontier-currentness",
    ])

    assert code == 0
    capsys.readouterr()
    name, kwargs = FakeSession.calls[0]
    assert name == "continue"
    assert callable(kwargs["frontier_currentness_provider"])

def test_host_frontier_status_can_classify_wave_currentness(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    wave = ROOT / "portfolio" / "advancement_wave.public.json"

    code = portal_cli.entrypoint([
        "host", "frontier-advertise",
        "--state-db", str(state_db),
        "--wave", str(wave),
        "--subject-id", "project-runner",
        "--ref", "main",
        "--exact-head", "a" * 40,
        "--ttl-seconds", "300",
    ])
    assert code == 0
    capsys.readouterr()

    class FakeTransport:
        def __init__(self, *, token=None):
            pass

        def read_ref(self, repository, ref):
            if repository == "thebrazenbeard/project-runner":
                return "b" * 40
            return "a" * 40

    monkeypatch.setattr(
        portal_cli,
        "GitHubRestTransport",
        FakeTransport,
    )

    code = portal_cli.entrypoint([
        "host", "frontier-status",
        "--state-db", str(state_db),
        "--wave", str(wave),
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    classification = payload["classification"]
    project_runner = next(
        item
        for item in classification["excluded_subjects"]
        if item["subject_id"] == "project-runner"
    )
    assert project_runner == {
        "subject_kind": "repository",
        "subject_id": "project-runner",
        "reason": "STALE_FRONTIER_HEAD",
    }

