from __future__ import annotations

import json
from pathlib import Path

import portal.cli as cli


class FakeSession:
    calls: list[tuple[str, object]] = []

    def __init__(self, db: Path):
        self.db = db

    def status(self, session_id: str):
        self.calls.append(("status", session_id))
        return {
            "session_id": session_id,
            "holder": "saved-owner",
            "control_state": "RUNNING",
            "summary": {"active": 0, "held": 0, "terminal": 0},
            "subjects": [],
        }

    def load_resume_spec(self, *, session_id: str, holder: str):
        self.calls.append(("load_resume_spec", (session_id, holder)))
        return {"verified": "existing-config"}

    def close(self):
        pass


def test_advance_rejects_inferred_project_scope(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PortalCommandSession", FakeSession)
    db = tmp_path / "state.sqlite3"
    db.touch()
    code = cli.entrypoint([
        "advance", "fix all repos and install",
        "--state-db", str(db),
    ])
    assert code == 2
    assert "exact scoped mission" in capsys.readouterr().err
    assert not FakeSession.calls


def test_advance_refuses_uninitialized_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PortalCommandSession", FakeSession)
    FakeSession.calls.clear()
    code = cli.entrypoint([
        "advance", "--state-db", str(tmp_path / "missing.sqlite3"),
    ])
    assert code == 2
    assert "no durable portfolio session" in capsys.readouterr().err
    assert not FakeSession.calls

def test_advance_preview_does_not_invoke_runner(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PortalCommandSession", FakeSession)
    FakeSession.calls.clear()
    db = tmp_path / "state.sqlite3"
    db.touch()
    monkeypatch.setattr(
        cli, "_continue_payload",
        lambda _args: (_ for _ in ()).throw(AssertionError("dispatched")),
    )
    code = cli.entrypoint([
        "advance", "advance my portfolio", "--session-id", "portfolio",
        "--state-db", str(db), "--dry-run",
    ])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dispatched"] is False
    assert result["semantic_goal"] == "ADVANCE_EXISTING_PORTFOLIO"
    assert FakeSession.calls == [
        ("status", "portfolio"),
        ("load_resume_spec", ("portfolio", "saved-owner")),
    ]

def test_advance_uses_saved_bounded_continuation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PortalCommandSession", FakeSession)
    db = tmp_path / "state.sqlite3"
    db.touch()
    called = []
    monkeypatch.setattr(
        cli, "_continue_payload",
        lambda args: called.append(args) or {"executed": "bounded"},
    )
    assert cli.entrypoint(["advance", "--state-db", str(db)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["bounded_cycle"] is True
    assert called[0].resume is True
    assert called[0].holder == "saved-owner"
    assert called[0].session_id == "portfolio"


def test_advance_explicit_continuous_uses_saved_guarded_loop(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(cli, "PortalCommandSession", FakeSession)
    db = tmp_path / "state.sqlite3"
    db.touch()
    called = []
    monkeypatch.setattr(
        cli, "_session_run_payload",
        lambda args: called.append(args) or {"executed": "guarded-loop"},
    )

    assert cli.entrypoint([
        "advance", "--continuous", "--state-db", str(db),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["bounded_cycle"] is False
    assert called[0].resume is True
    assert called[0].holder == "saved-owner"
    assert called[0].command == "run"
