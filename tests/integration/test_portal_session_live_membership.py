from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import portal.cli as portal_cli
from portal.discovery import RepositoryInventoryItem, build_live_project_registry


ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def run_until_idle(self, **kwargs):
        self.calls.append(("run", kwargs))
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


def _snapshot():
    return build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            RepositoryInventoryItem(
                name="project-runner",
                full_name="thebrazenbeard/project-runner",
                private=False,
                archived=False,
                default_branch="main",
            ),
            RepositoryInventoryItem(
                name="vera-mono",
                full_name="thebrazenbeard/vera-mono",
                private=False,
                archived=False,
                default_branch="main",
            ),
        ),
        curated_projects=(),
    )


def test_safe_host_session_auto_refreshes_live_membership(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    discovered: list[str] = []
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(
        portal_cli,
        "discover_live_project_registry",
        lambda *, owner, **_kwargs: (
            discovered.append(owner) or _snapshot()
        ),
    )

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--host-bridge",
        "--host-frontier-currentness",
        "--max-cycles", "1",
    ])

    assert code == 0
    capsys.readouterr()
    assert discovered == ["thebrazenbeard"]
    name, kwargs = FakeSession.calls[0]
    assert name == "run"
    assert kwargs["projects_path"] == tmp_path / "projects.live.yaml"
    assert kwargs["projects_path"].exists()


def test_safe_host_continue_auto_refreshes_live_membership(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    discovered: list[str] = []
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(
        portal_cli,
        "discover_live_project_registry",
        lambda *, owner, **_kwargs: (
            discovered.append(owner) or _snapshot()
        ),
    )

    code = portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--host-bridge",
        "--host-frontier-currentness",
    ])

    assert code == 0
    capsys.readouterr()
    assert discovered == ["thebrazenbeard"]
    name, kwargs = FakeSession.calls[0]
    assert name == "continue"
    assert kwargs["projects_path"] == tmp_path / "projects.live.yaml"
    assert kwargs["projects_path"].exists()


def test_static_projects_explicitly_disables_safe_host_auto_discovery(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    def forbidden(**_kwargs):
        raise AssertionError("live discovery must be disabled")

    monkeypatch.setattr(portal_cli, "discover_live_project_registry", forbidden)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--host-bridge",
        "--host-frontier-currentness",
        "--static-projects",
        "--max-cycles", "1",
    ])

    assert code == 0
    capsys.readouterr()
    _name, kwargs = FakeSession.calls[0]
    assert kwargs["projects_path"] == ROOT / "registry" / "projects.yaml"
