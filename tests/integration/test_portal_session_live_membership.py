from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace

import portal.cli as portal_cli
import portal.session as portal_session
from portal.discovery import RepositoryInventoryItem
from portal.frontier_currentness import (
    PortalFrontierObservation,
    PortalHostFrontierStore,
    frontier_policy_sha256,
)
from portal.live_portfolio import refresh_live_local_portfolio
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_wave_scheduler import (
    WaveExecutionBudget,
    plan_wave_admission,
)


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


class FakeCatalog:
    calls: list[str] = []

    def __init__(self, *, token=None) -> None:
        self.token = token

    def list_owned_repositories(self, owner: str):
        self.calls.append(owner)
        return (
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
        )


def test_safe_host_session_auto_refreshes_live_membership(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    FakeCatalog.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(portal_cli, "GitHubRepositoryCatalog", FakeCatalog)

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
    assert FakeCatalog.calls == ["thebrazenbeard"]
    name, kwargs = FakeSession.calls[0]
    assert name == "run"
    assert kwargs["projects_path"] == tmp_path / "projects.live.yaml"
    assert kwargs["projects_path"].exists()
    assert kwargs["public_safe"] is False


def test_safe_host_continue_auto_refreshes_live_membership(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    FakeCatalog.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(portal_cli, "GitHubRepositoryCatalog", FakeCatalog)

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
    assert FakeCatalog.calls == ["thebrazenbeard"]
    name, kwargs = FakeSession.calls[0]
    assert name == "continue"
    assert kwargs["projects_path"] == tmp_path / "projects.live.yaml"
    assert kwargs["projects_path"].exists()
    assert kwargs["public_safe"] is False


def test_static_projects_explicitly_disables_safe_host_auto_discovery(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    FakeSession.calls.clear()
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    class ForbiddenCatalog:
        def __init__(self, **_kwargs):
            raise AssertionError("live discovery must be disabled")

    monkeypatch.setattr(portal_cli, "GitHubRepositoryCatalog", ForbiddenCatalog)

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
    assert kwargs["public_safe"] is True


def test_selected_host_rejects_stale_head_before_new_wave_admission(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The real CLI -> currentness -> session -> planner path must hold drift."""
    state_db = tmp_path / "portal.sqlite3"
    repos = FakeCatalog().list_owned_repositories("thebrazenbeard")
    seed = refresh_live_local_portfolio(
        baseline_corpus_path=ROOT / "portfolio" / "corpus.public.json",
        baseline_wave_path=ROOT / "portfolio" / "advancement_wave.public.json",
        repositories=repos,
        observed_at="2026-10-24T00:00:00Z",
        output_dir=tmp_path / "seed-local-only",
    )
    wave = load_advancement_wave(seed.wave_path)
    item = next(
        item for item in wave.items
        if item.subject_kind == "repository" and item.repositories == ("thebrazenbeard/project-runner",)
    )
    assert item.execution_state == "QUEUED"
    assert item.action == "EXECUTE_FRONTIER"
    assert item.effect_ceiling == "SOURCE_ONLY"
    budget = WaveExecutionBudget(100, 100, 100, 100)
    control = plan_wave_admission(wave, budget=budget)
    assert item.subject_id in {selected.subject_id for selected in control.selected}
    held = plan_wave_admission(
        wave,
        budget=budget,
        excluded_subjects=((item.subject_kind, item.subject_id),),
    )
    assert item.subject_id not in {selected.subject_id for selected in held.selected}

    store = PortalHostFrontierStore(state_db)
    store.advertise(
        PortalFrontierObservation(
            subject_kind=item.subject_kind,
            subject_id=item.subject_id,
            repository=item.repositories[0],
            ref="main",
            exact_head="a" * 40,
            frontier_sha256=frontier_policy_sha256(item),
            disposition="CURRENT",
        ),
        ttl_seconds=600.0,
        observed_at=time.time(),
    )
    store.close()

    class MovedHeadTransport:
        reads: list[tuple[str, str]] = []

        def __init__(self, *, token=None) -> None:
            pass

        def read_ref(self, repository, ref):
            self.reads.append((repository, ref))
            return "b" * 40

    prepared = []
    real_prepare = portal_session.prepare_portal_wave

    def observe_prepare(**kwargs):
        result = real_prepare(**kwargs)
        prepared.append((kwargs, result))
        return result

    monkeypatch.setattr(portal_cli, "GitHubRepositoryCatalog", FakeCatalog)
    monkeypatch.setattr(portal_cli, "GitHubRestTransport", MovedHeadTransport)
    monkeypatch.setattr(portal_session, "prepare_portal_wave", observe_prepare)

    assert portal_cli.entrypoint([
        "run", "--once",
        "--session-id", "head-drift",
        "--state-db", str(state_db),
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--host-bridge", "--host-frontier-currentness",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["session_id"] == "head-drift"
    assert ("thebrazenbeard/project-runner", "main") in MovedHeadTransport.reads
    assert len(prepared) == 1
    kwargs, plan = prepared[0]
    assert ("repository", item.subject_id) in kwargs["excluded_subjects"]
    assert plan.assigned == 0
    assert plan.claimed == 0
    assert plan.packets == ()
