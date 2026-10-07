import json
from types import SimpleNamespace

import pytest

from portal.desktop_portfolio import DesktopPortfolioController
from portal.session import PortalCommandSession


def test_portfolio_profile_reuses_durable_runner_controls(tmp_path, monkeypatch):
    paths = {}
    for name in ("wave", "corpus", "projects", "nodes"):
        path = tmp_path / (name + ".json")
        path.write_text("{}")
        paths[name] = str(path)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({**paths, "max_parallel": 2}))
    captured = []
    def prepare(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(packets=())
    monkeypatch.setattr("portal.session.prepare_portal_wave", prepare)
    monkeypatch.setattr("portal.desktop_portfolio.load_execution_nodes", lambda path: ())
    session = PortalCommandSession(tmp_path / "session.sqlite3")
    try:
        controller = DesktopPortfolioController(tmp_path, session=session)
        assert controller.handle({"action": "status"})["configured"] is False
        controller.handle({"action": "configure", "profile_path": str(profile)})
        result = controller.handle({"action": "run", "session_id": "portfolio"})
        assert result["session"]["control_state"] == "RUNNING"
        assert result["session"]["generation"] == 1
        assert captured[0]["budget"].max_parallel == 2
        assert result["protected_effect_authority"] is False
        controller.handle({"action": "hold", "session_id": "portfolio", "subject_id": "owner/repo"})
        assert session.status("portfolio")["subjects"][0]["hold_requested"] is True
        assert controller.handle({"action": "stop", "session_id": "portfolio"})["session"]["control_state"] == "STOPPED"
        with pytest.raises(ValueError, match="stopped"):
            controller.handle({"action": "continue", "session_id": "portfolio"})
        assert captured[0]["token"] is None
    finally:
        session.close()


def test_profile_cannot_grant_effects_or_paid_execution(tmp_path):
    profile = tmp_path / "unsafe.json"
    profile.write_text(json.dumps({"protected_effect_authority": True, "worker_command": "arbitrary"}))
    session = PortalCommandSession(tmp_path / "session.sqlite3")
    try:
        controller = DesktopPortfolioController(tmp_path, session=session)
        with pytest.raises(ValueError, match="unsupported"):
            controller.handle({"action": "configure", "profile_path": str(profile)})
        assert not (tmp_path / "PORTFOLIO_PROFILE.json").exists()
    finally:
        session.close()


@pytest.mark.parametrize("missing_file", ["wave", "corpus", "projects", "nodes"])
def test_durable_controls_survive_missing_profile_inputs(tmp_path, monkeypatch, missing_file):
    paths = {}
    for name in ("wave", "corpus", "projects", "nodes"):
        path = tmp_path / (name + ".json")
        path.write_text("{}")
        paths[name] = str(path)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(paths))
    prepared = []
    def prepare(**kwargs):
        prepared.append(kwargs)
        return SimpleNamespace(packets=())
    monkeypatch.setattr("portal.session.prepare_portal_wave", prepare)
    monkeypatch.setattr("portal.desktop_portfolio.load_execution_nodes", lambda path: ())
    session = PortalCommandSession(tmp_path / "session.sqlite3")
    try:
        controller = DesktopPortfolioController(tmp_path, session=session)
        controller.handle({"action": "configure", "profile_path": str(profile)})
        controller.handle({"action": "run", "session_id": "durable"})
        (tmp_path / (missing_file + ".json")).unlink()
        status = controller.handle({"action": "status", "session_id": "durable"})
        assert status["session"]["control_state"] == "RUNNING"
        assert status["configured"] is False
        assert missing_file in status["profile_error"]
        held = controller.handle({"action": "hold", "session_id": "durable", "subject_id": "owner/repo"})
        assert held["session"]["subjects"][0]["hold_requested"] is True
        stopped = controller.handle({"action": "stop", "session_id": "durable"})
        assert stopped["session"]["control_state"] == "STOPPED"
        assert stopped["dispatch_mode"] == "ADMISSION_ONLY"
        assert stopped["protected_effect_authority"] is False
        with pytest.raises(ValueError, match="file is missing"):
            controller.handle({"action": "run", "session_id": "durable"})
        assert len(prepared) == 1
    finally:
        session.close()


def test_durable_stop_survives_malformed_saved_profile(tmp_path, monkeypatch):
    session = PortalCommandSession(tmp_path / "session.sqlite3")
    try:
        session._ensure_session(session_id="durable", holder="known-holder", now=0)
        (tmp_path / "PORTFOLIO_PROFILE.json").write_text("{malformed")
        controller = DesktopPortfolioController(tmp_path, session=session)
        result = controller.handle({"action": "stop", "session_id": "durable"})
        assert result["session"]["control_state"] == "STOPPED"
        assert result["session"]["holder"] == "known-holder"
        assert "JSONDecodeError" in result["profile_error"]
    finally:
        session.close()
