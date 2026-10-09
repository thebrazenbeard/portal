import json
import os
from pathlib import Path
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


def _desktop_runtime_sources(tmp_path):
    source = tmp_path / "sources" / "portal"
    (source / "portfolio").mkdir(parents=True)
    (source / "registry").mkdir(parents=True)
    (source / "portfolio" / "advancement_wave.public.json").write_text(
        json.dumps(
            {
                "schema": "PORTFOLIO_ADVANCEMENT_WAVE_V1",
                "corpus_binding": {"sha256": "0" * 64},
                "items": [
                    {
                        "subject_kind": "repository",
                        "subject_id": "portal",
                        "repository": "thebrazenbeard/portal",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (source / "portfolio" / "corpus.public.json").write_text(
        json.dumps({"schema": "PORTFOLIO_CORPUS_V1", "records": []}),
        encoding="utf-8",
    )
    (source / "registry" / "projects.yaml").write_text(
        "projects: []\n",
        encoding="utf-8",
    )
    return source


def test_run_without_profile_bootstraps_live_runtime_profile(tmp_path, monkeypatch):
    _desktop_runtime_sources(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    resolved = {}
    for name in ("codex", "gh", "git"):
        executable = tools / (name + ".exe")
        executable.write_text("", encoding="utf-8")
        resolved[name] = str(executable)

    session = PortalCommandSession(tmp_path / "state" / "portal" / "session.sqlite3")
    try:
        controller = DesktopPortfolioController(
            tmp_path,
            session=session,
            executable_resolver=lambda name: resolved.get(name),
            token_provider=lambda: "existing-token",
        )
        captured = {}

        def run(**kwargs):
            captured.update(kwargs)
            session._ensure_session(
                session_id=kwargs["session_id"],
                holder=kwargs["holder"],
                now=0,
            )
            return SimpleNamespace(packets=())

        monkeypatch.setattr(session, "run", run)
        monkeypatch.setattr(
            "portal.desktop_portfolio.load_execution_nodes",
            lambda path: (),
        )
        monkeypatch.setattr(
            controller,
            "_refresh_live_inputs",
            lambda profile: (
                Path(profile["wave"]),
                Path(profile["corpus"]),
                Path(profile["projects"]),
                {"public": 67, "private": 19, "archived": 5},
            ),
        )
        monkeypatch.setattr(
            controller,
            "_execution_adapter",
            lambda profile, nodes, token, *, pins=None: "proposal-adapter",
        )

        result = controller.handle(
            {"action": "run", "session_id": "portfolio"}
        )

        profile = result["profile"]
        assert result["configured"] is True
        assert profile["mode"] == "LIVE_AUTO_V1"
        assert profile["discover_owner"] == "thebrazenbeard"
        assert profile["discovered_effect_ceiling"] == "SOURCE_ONLY"
        assert Path(profile["nodes"]).is_file()
        assert Path(profile["worker_backends"]).is_file()
        import yaml
        worker_manifest = yaml.safe_load(
            Path(profile["worker_backends"]).read_text(encoding="utf-8")
        )
        assert worker_manifest["workers"][0]["pass_env"] == (
            ["APPDATA", "GH_CONFIG_DIR"]
            if os.name == "nt"
            else ["GH_CONFIG_DIR", "XDG_CONFIG_HOME"]
        )
        assert result["portfolio_source"] == "LIVE_GITHUB"
        assert result["inventory"] == {
            "public": 67,
            "private": 19,
            "archived": 5,
        }
        assert result["worker_state"] == "CONFIGURED"
        assert captured["execution_adapter"] == "proposal-adapter"
        assert captured["public_safe"] is False
    finally:
        session.close()


def test_auto_bootstrap_reports_missing_worker_without_faking_attachment(
    tmp_path,
    monkeypatch,
):
    _desktop_runtime_sources(tmp_path)
    session = PortalCommandSession(tmp_path / "state" / "portal" / "session.sqlite3")
    try:
        controller = DesktopPortfolioController(
            tmp_path,
            session=session,
            executable_resolver=lambda name: None if name == "codex" else name,
            token_provider=lambda: None,
        )
        profile = controller._bootstrap_live_profile()

        assert profile["mode"] == "LIVE_AUTO_V1"
        assert profile["worker_backends"] is None
        status = controller.handle({"action": "status"})
        assert status["configured"] is True
        assert status["worker_state"] == "UNAVAILABLE"
        assert status["missing_worker_tools"] == ["codex"]
        assert status["dispatch_mode"] == "ADMISSION_ONLY"
    finally:
        session.close()



def test_live_auto_refuses_silent_public_only_inventory_downgrade(
    tmp_path,
    monkeypatch,
):
    _desktop_runtime_sources(tmp_path)
    session = PortalCommandSession(
        tmp_path / "state" / "portal" / "session.sqlite3"
    )
    try:
        controller = DesktopPortfolioController(
            tmp_path,
            session=session,
            executable_resolver=lambda name: None,
            token_provider=lambda: None,
        )

        class UnexpectedPublicCatalog:
            def __init__(self, *, token):
                raise AssertionError(
                    "unauthenticated public listing must not be attempted"
                )

        monkeypatch.setattr(
            "portal.desktop_portfolio.GitHubRepositoryCatalog",
            UnexpectedPublicCatalog,
        )
        with pytest.raises(ValueError, match="authenticated GitHub"):
            controller.handle(
                {"action": "run", "session_id": "portfolio"}
            )

        assert session.connection.execute(
            "SELECT COUNT(*) FROM portal_command_sessions"
        ).fetchone()[0] == 0
    finally:
        session.close()


def test_autopilot_inspect_and_generation_cas_fail_closed(tmp_path, monkeypatch):
    paths = {}
    for name in ("wave", "corpus", "projects", "nodes"):
        item = tmp_path / (name + ".json")
        item.write_text("{}", encoding="utf-8")
        paths[name] = str(item)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(paths), encoding="utf-8")
    monkeypatch.setattr(
        "portal.session.prepare_portal_wave",
        lambda **kwargs: SimpleNamespace(packets=()),
    )
    monkeypatch.setattr(
        "portal.desktop_portfolio.load_execution_nodes", lambda path: ()
    )
    session = PortalCommandSession(tmp_path / "portal.sqlite3")
    try:
        controller = DesktopPortfolioController(tmp_path, session=session)
        controller.handle({"action": "configure", "profile_path": str(profile)})
        initial = controller.handle({"action": "run", "session_id": "portfolio"})
        assert initial["session"]["generation"] == 1
        fresh = controller.handle({"action": "inspect", "session_id": "portfolio"})
        assert fresh["session"]["generation"] == 1
        holder = fresh["session"]["holder"]
        with pytest.raises(ValueError, match="stale portfolio generation"):
            controller.handle({"action": "continue", "session_id": "portfolio",
                               "expected_generation": 0,
                               "expected_holder": holder})
        with pytest.raises(ValueError, match="holder changed"):
            controller.handle({"action": "continue", "session_id": "portfolio",
                               "expected_generation": 1,
                               "expected_holder": "other-worker"})
        assert controller.handle({"action": "inspect"})["session"]["generation"] == 1
        controller.handle({"action": "continue", "session_id": "portfolio",
                           "expected_generation": 1, "expected_holder": holder})
        assert controller.handle({"action": "inspect"})["session"]["generation"] == 2
    finally:
        session.close()
