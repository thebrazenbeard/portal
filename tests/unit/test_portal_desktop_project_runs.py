from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from portal import desktop_project_runs as mod


SOURCE = Path(__file__).resolve().parents[2]
REPO = "thebrazenbeard/physica"
HEAD = "a33e3a8c94787cc884dd7c668b709ff1ec945551"


def _fake_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "source"
    (checkout / "src" / "physica").mkdir(parents=True)
    (checkout / "src" / "physica" / "__main__.py").write_text("")
    return checkout


def test_specific_approved_profile_only() -> None:
    profile = mod.load_project_run_profile(SOURCE, REPO)
    assert profile is not None
    assert profile["expected_head"] == HEAD
    assert profile["local_simulation_only"] is True
    assert mod.load_project_run_profile(SOURCE, "thebrazenbeard/portal") is None
    assert mod.load_project_run_profile(SOURCE, "--evil") is None


def test_profile_data_cannot_inject_unapproved_module_or_argv(tmp_path: Path) -> None:
    path = tmp_path / "registry" / "desktop_run_profiles_v1.json"
    path.parent.mkdir()
    source = mod.load_project_run_profile(SOURCE, REPO)
    assert source is not None
    for patch in (
        {"module": "os"},
        {"args": ["-c", "import os;os.system('bad')"]},
        {"local_simulation_only": False},
        {"expected_head": "not-pinned"},
    ):
        item = dict(source, **patch)
        path.write_text(json.dumps({
            "schema": "PORTAL_DESKTOP_RUN_PROFILE_V1", "profiles": [item]
        }))
        with pytest.raises(ValueError, match="unapproved"):
            mod.load_project_run_profile(tmp_path, REPO)


def test_unqualified_main_change_fails_before_process_or_receipts(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(mod, "inspect_repository_head", lambda repo: "b" * 40)
    with pytest.raises(RuntimeError, match="main changed"):
        mod.run_selected_local_project(REPO, SOURCE, tmp_path)
    assert not (tmp_path / "state").exists()


def test_bounded_real_project_command_captures_durable_receipts_without_credentials(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(mod, "inspect_repository_head", lambda repo: HEAD)
    checkout = _fake_checkout(tmp_path)
    monkeypatch.setattr(mod, "_pinned_checkout", lambda repo, sha, cache: checkout)
    captured = []

    def fake_process(argv, *, cwd, timeout, env=None):
        captured.append((argv, cwd, timeout, env))
        assert argv[0] and argv[1:4] == ["-B", "-m", "physica"]
        assert "--summary-only" in argv
        assert timeout == 25.0
        assert env["PYTHONPATH"] == str(checkout / "src")
        assert "OPENAI_API_KEY" not in env
        assert "PORTAL_GITHUB_TOKEN" not in env
        assert (cwd / "receipt.json").exists()
        assert json.loads((cwd / "receipt.json").read_text())["state"] == "ATTEMPTED"
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout=json.dumps({
                "schema": "physica.simulation.v1", "model": "oscillator",
                "sha256": "c" * 64, "steps": 10
            }),
            stderr="",
        )

    monkeypatch.setattr(mod, "_run_argv", fake_process)
    result = mod.run_selected_local_project(REPO, SOURCE, tmp_path)
    assert result["state"] == "VERIFIED_COMPLETE"
    assert result["source_sha"] == HEAD
    assert result["steps"] == 10
    assert result["automatic_retry"] is False
    persisted = json.loads(Path(result["evidence_path"]).read_text())
    assert persisted["run_id"] == result["run_id"]
    assert persisted["state"] == "VERIFIED_COMPLETE"
    assert len(captured) == 1


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("failed", "VERIFIED_HELD"),
        ("timeout", "OUTCOME_UNKNOWN"),
        ("invalid", "VERIFIED_HELD"),
    ],
)
def test_failed_or_ambiguous_launch_is_preserved_not_retried(
    monkeypatch, tmp_path: Path, mode, expected,
) -> None:
    monkeypatch.setattr(mod, "inspect_repository_head", lambda repo: HEAD)
    checkout = _fake_checkout(tmp_path)
    monkeypatch.setattr(mod, "_pinned_checkout", lambda repo, sha, cache: checkout)
    calls = []

    def fake_process(argv, *, cwd, timeout, env=None):
        calls.append(argv)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        if mode == "failed":
            return subprocess.CompletedProcess(
                argv, 6, stdout="", stderr="Sim failure",
            )
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"schema":"made.up"}', stderr="",
        )
    monkeypatch.setattr(mod, "_run_argv", fake_process)
    result = mod.run_selected_local_project(REPO, SOURCE, tmp_path)
    assert result["state"] == expected
    assert len(calls) == 1
    final = json.loads(Path(result["evidence_path"]).read_text())
    assert final["state"] == expected
    assert final["automatic_retry"] is False


def test_arbitrary_repository_cannot_run_even_with_local_registry(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(mod, "inspect_repository_head", lambda repo: pytest.fail("no network"))
    with pytest.raises(ValueError, match="no qualified"):
        mod.run_selected_local_project("thebrazenbeard/vera-mono", SOURCE, tmp_path)



def test_windows_selected_project_button_is_gated_by_exact_profile(monkeypatch) -> None:
    from portal.desktop_app import PortalDesktopApp
    from portal.desktop_portfolio import DesktopProject

    class Listbox:
        def __init__(self): self.selection = (0,)
        def curselection(self): return self.selection

    class Button:
        state = None
        def configure(self, **kwargs): self.state = kwargs.get("state")

    class Label:
        text = ""
        def set(self, value): self.text = value

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._source_root = SOURCE
    app.project_listbox = Listbox()
    app.project_run_button = Button()
    app.project_detail_var = Label()
    app._project_items = (
        DesktopProject(
            id="physica", name="Physica", repository=REPO,
            visibility="public", scheduling_state="UNCURATED", targets=(),
        ),
        DesktopProject(
            id="vera-mono", name="Vera Mono",
            repository="thebrazenbeard/vera-mono",
            visibility="private", scheduling_state="UNCURATED", targets=(),
        ),
    )
    app._project_selected(None)
    assert app.project_run_button.state == "normal"
    assert "physica-oscillator-smoke" in app.project_detail_var.text
    app.project_listbox.selection = (1,)
    app._project_selected(None)
    assert app.project_run_button.state == "disabled"
    assert "No qualified local run profile" in app.project_detail_var.text


def test_selected_project_action_surfaces_durable_receipt_not_fake_success(
    monkeypatch, tmp_path: Path,
) -> None:
    from portal.desktop_app import PortalDesktopApp
    from portal.desktop_portfolio import DesktopProject

    class Listbox:
        def curselection(self): return (0,)

    class Status:
        message = ""
        def set(self, value): self.message = value

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._source_root = SOURCE
    app._runtime_root = tmp_path
    app.project_listbox = Listbox()
    app.portfolio_action_var = Status()
    app._project_items = (
        DesktopProject(
            id="physica", name="Physica", repository=REPO,
            visibility="public", scheduling_state="UNCURATED", targets=(),
        ),
    )
    observed = []

    def fake_run(repository, source_root, runtime_root):
        assert repository == REPO
        assert runtime_root == tmp_path
        return {
            "state": "VERIFIED_COMPLETE",
            "run_id": "b" * 32,
            "evidence_path": str(tmp_path / "evidence.json"),
        }

    monkeypatch.setattr("portal.desktop_app.run_selected_local_project", fake_run)
    app._start_portfolio_action = lambda title, operation: observed.append(
        (title, operation())
    )
    app._run_selected_project()
    assert len(observed) == 1
    assert "physica-oscillator-smoke" in observed[0][0]
    assert "VERIFIED_COMPLETE" in observed[0][1]
    assert "evidence.json" in observed[0][1]


def test_local_receipt_history_has_verifiable_states_without_retries(tmp_path: Path) -> None:
    import json
    base=tmp_path/"state"/"portal"/"project-runs"/"receipts"
    successful="a"*32
    failed="b"*32
    abandoned="c"*32
    for run_id, state, started in (
        (successful,"VERIFIED_COMPLETE",100.0),
        (failed,"VERIFIED_HELD",150.0),
        (abandoned,"ATTEMPTED",50.0),
    ):
        d=base/run_id
        d.mkdir(parents=True)
        data={
            "schema":"PORTAL_DESKTOP_PROJECT_RUN_RECEIPT_V1",
            "run_id":run_id,
            "repository":REPO,
            "source_sha":HEAD,
            "state":state,
            "started_at":started,
        }
        name="receipt.json" if state=="ATTEMPTED" else "final.json"
        (d/name).write_text(json.dumps(data))
    results=mod.list_project_run_receipts(tmp_path,limit=3,now=999.0)
    assert [r["run_id"] for r in results] == [failed, successful, abandoned]
    assert results[-1]["state"] == "OUTCOME_UNKNOWN_NOT_FINALIZED"
    assert mod.list_project_run_receipts(tmp_path,limit=1,now=999.0)[0]["run_id"] == failed
    with pytest.raises(ValueError):
        mod.list_project_run_receipts(tmp_path,limit=0)
