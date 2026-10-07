from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

import portal.cli as portal_cli
from portal.discovery import RepositoryInventoryItem


ROOT = Path(__file__).resolve().parents[2]


class _CaptureSession:
    calls: list[dict[str, object]] = []

    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def save_resume_spec(self, **kwargs) -> None:
        pass

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            session_id=kwargs["session_id"],
            control_state="RUNNING",
            generation=1,
            wave_run_id=f"{kwargs['session_id']}::g1",
            packets=(),
            summary={"active": 0, "held": 0, "terminal": 0},
        )


def _repo(
    name: str,
    *,
    private: bool = False,
    archived: bool = False,
) -> RepositoryInventoryItem:
    return RepositoryInventoryItem(
        name=name,
        full_name=f"thebrazenbeard/{name}",
        private=private,
        archived=archived,
        default_branch="main",
    )


def test_session_portfolio_paths_refresh_membership_wave_and_corpus_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = {"count": 0}

    class FakeCatalog:
        def __init__(self, *, token=None):
            self.token = token

        def list_owned_repositories(self, owner: str):
            calls["count"] += 1
            assert owner == "thebrazenbeard"
            return (
                _repo("project-runner"),
                _repo("new-live"),
                _repo("old-live", archived=True),
                _repo("private-live", private=True),
            )

    monkeypatch.setattr(
        portal_cli,
        "GitHubRepositoryCatalog",
        FakeCatalog,
        raising=False,
    )

    args = SimpleNamespace(
        projects=ROOT / "registry" / "projects.yaml",
        static_projects=False,
        discover_owner="thebrazenbeard",
        host_bridge=False,
        host_frontier_currentness=False,
        discovered_effect_ceiling="SOURCE_ONLY",
        wave=ROOT / "portfolio" / "advancement_wave.public.json",
        corpus=ROOT / "portfolio" / "corpus.public.json",
        state_db=tmp_path / "portal.sqlite3",
        write_live_registry=None,
    )

    wave_path, corpus_path, projects_path = portal_cli._session_portfolio_paths(args)

    assert calls["count"] == 1
    assert wave_path != args.wave
    assert corpus_path != args.corpus
    assert projects_path != args.projects

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    local_repos = {item["repository"] for item in corpus["records"]}
    assert local_repos == {
        "thebrazenbeard/project-runner",
        "thebrazenbeard/new-live",
        "thebrazenbeard/old-live",
        "thebrazenbeard/private-live",
    }
    assert corpus["counts"] == {
        "total": 4,
        "public": 3,
        "private": 1,
        "archived": 1,
        "public_archived": 1,
        "private_archived": 0,
    }
    private_record = next(
        item
        for item in corpus["records"]
        if item["repository"] == "thebrazenbeard/private-live"
    )
    assert private_record["visibility"] == "private"

    wave = json.loads(wave_path.read_text(encoding="utf-8"))
    by_repo = {
        item["repository"]: item
        for item in wave["items"]
        if item["subject_kind"] == "repository"
    }
    assert by_repo["thebrazenbeard/new-live"]["action"] == "EXECUTE_FRONTIER"
    assert by_repo["thebrazenbeard/new-live"]["effect_ceiling"] == "SOURCE_ONLY"
    assert by_repo["thebrazenbeard/new-live"]["review_gate"] == "EXACT_HEAD_REVIEW"
    assert by_repo["thebrazenbeard/old-live"]["execution_state"] == "HELD"
    assert by_repo["thebrazenbeard/old-live"]["effect_ceiling"] == "NO_EFFECT"
    assert by_repo["thebrazenbeard/private-live"]["action"] == "EXECUTE_FRONTIER"
    assert by_repo["thebrazenbeard/private-live"]["effect_ceiling"] == "SOURCE_ONLY"
    assert by_repo["thebrazenbeard/private-live"]["review_gate"] == "EXACT_HEAD_REVIEW"
    assert by_repo["thebrazenbeard/private-live"]["execution_state"] == "QUEUED"

    registry = yaml.safe_load(projects_path.read_text(encoding="utf-8"))
    registered = {
        repo
        for project in registry["projects"]
        for repo in project["repositories"]
    }
    assert "thebrazenbeard/private-live" in registered
    assert portal_cli._session_portfolio_public_safe(args) is False


def test_command_session_uses_refreshed_live_portfolio_paths(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _CaptureSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", _CaptureSession)

    live_wave = tmp_path / "live-wave.json"
    live_corpus = tmp_path / "live-corpus.json"
    live_projects = tmp_path / "live-projects.yaml"

    monkeypatch.setattr(
        portal_cli,
        "_session_portfolio_paths",
        lambda args: (live_wave, live_corpus, live_projects),
        raising=False,
    )

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--once",
    ])

    assert code == 0
    capsys.readouterr()
    assert len(_CaptureSession.calls) == 1
    call = _CaptureSession.calls[0]
    assert call["wave_path"] == live_wave
    assert call["corpus_path"] == live_corpus
    assert call["projects_path"] == live_projects
    assert call["public_safe"] is True
