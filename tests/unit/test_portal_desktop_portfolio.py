from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from portal.desktop_portfolio import (
    discover_github_portfolio,
    inspect_repository_head,
    load_curated_portfolio,
    run_governed_portfolio_cycle,
)


ROOT = Path(__file__).resolve().parents[2]


def test_curated_registry_not_mistaken_for_live_portfolio() -> None:
    entries = load_curated_portfolio(ROOT)
    assert len(entries) == 15
    assert any(x.id == "project-runner" for x in entries)
    assert all(x.repository.startswith("thebrazenbeard/") for x in entries)
    assert not any(x.id == "physica" for x in entries)


def test_authenticated_live_inventory_includes_uncurated_active_and_excludes_archived(
    monkeypatch,
) -> None:
    from portal import desktop_portfolio as mod

    items = [
        {"nameWithOwner":"thebrazenbeard/physica","isArchived":False,"isPrivate":False},
        {"nameWithOwner":"thebrazenbeard/vera-mono","isArchived":False,"isPrivate":True},
        {"nameWithOwner":"thebrazenbeard/retired","isArchived":True,"isPrivate":False},
    ]
    def fake_run(cmd, **kwargs):
        assert cmd[:4] == ["gh", "repo", "list", "thebrazenbeard"]
        assert "--json" in cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps(items), stderr="")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = discover_github_portfolio(ROOT)
    assert {item.repository for item in result} == {
        "thebrazenbeard/physica", "thebrazenbeard/vera-mono",
    }
    physica = next(item for item in result if item.id=="physica")
    assert physica.scheduling_state == "UNCURATED"
    assert physica.targets == ()
    with pytest.raises(ValueError, match="owner"):
        discover_github_portfolio(ROOT, owner="../malicious")


def test_exact_head_inspection_is_read_only_and_validated(monkeypatch) -> None:
    from portal import desktop_portfolio as mod

    def fake_run(cmd, **kwargs):
        assert cmd == [
            "git","ls-remote","https://github.com/thebrazenbeard/physica.git",
            "refs/heads/main",
        ]
        return SimpleNamespace(returncode=0,stdout="a"*40+"\trefs/heads/main\n",stderr="")
    monkeypatch.setattr(mod.subprocess,"run",fake_run)
    assert inspect_repository_head("thebrazenbeard/physica") == "a"*40
    with pytest.raises(ValueError):
        inspect_repository_head("--upload-pack=malicious")


def test_bounded_cycle_executes_true_cli_no_effects_and_isolated_db(monkeypatch,tmp_path):
    from portal import desktop_portfolio as mod

    called={}
    def fake_run(command, **kwargs):
        called["command"] = command
        assert kwargs["cwd"] == ROOT
        assert "--once" in command
        assert "--host-bridge" in command
        assert "--static-projects" in command
        assert "NO_EFFECT" in command
        assert "--host-drivers" not in command
        assert "--worker-backends" not in command
        assert "--discover-owner" not in command
        assert str(tmp_path/"state"/"portal"/"desktop-portfolio.sqlite3") in command
        return SimpleNamespace(returncode=0,stdout=json.dumps({
            "summary":{"held":15},"protected_effects_authorized":False
        }),stderr="")
    monkeypatch.setattr(mod.subprocess,"run",fake_run)
    result=run_governed_portfolio_cycle(ROOT,tmp_path)
    assert result["summary"]["held"]==15
    assert called["command"][-1]=="NO_EFFECT"


def test_bounded_cycle_refuses_unqualified_route_and_false_authority(monkeypatch,tmp_path):
    from portal import desktop_portfolio as mod

    def denied(cmd, **kwargs):
        return SimpleNamespace(returncode=1,stdout="",stderr="portal: no qualified execution route")
    monkeypatch.setattr(mod.subprocess,"run",denied)
    with pytest.raises(RuntimeError,match="no qualified execution route"):
        run_governed_portfolio_cycle(ROOT,tmp_path)
    def lying(cmd, **kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps({
            "protected_effects_authorized":True
        }),stderr="")
    monkeypatch.setattr(mod.subprocess,"run",lying)
    with pytest.raises(RuntimeError,match="protected-effect denial"):
        run_governed_portfolio_cycle(ROOT,tmp_path)
