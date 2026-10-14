from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from portal.desktop_app import PortalDesktopApp
from portal.desktop_portfolio import DesktopProject
from portal.portfolio_relationships import relationship_hint, role_counts


def test_portfolio_role_hints_are_not_worker_bindings() -> None:
    assert "coordinator" in relationship_hint("thebrazenbeard/portal").lower()
    assert "kernel" in relationship_hint("thebrazenbeard/project-runner").lower()
    assert "simulation" in relationship_hint("thebrazenbeard/physica").lower()
    assert relationship_hint("thebrazenbeard/unknown-experimental-repo").startswith(
        "Unclassified:"
    )
    assert role_counts([
        "thebrazenbeard/portal", "thebrazenbeard/project-runner",
        "thebrazenbeard/unknown-experimental-repo"
    ]) == {"known_role":2,"needs_source_review":1}


def test_eight_lane_qualification_requires_authenticated_live_inventory(
    monkeypatch, tmp_path: Path,
) -> None:
    class Var:
        def __init__(self): self.message=""
        def set(self,value):self.message=value
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app._source_root=Path(__file__).resolve().parents[2]
    app._runtime_root=tmp_path
    app.portfolio_action_var=Var()
    app._live_inventory_loaded=False
    app._project_items=(
        DesktopProject("portal","PORTAL","thebrazenbeard/portal","public","UNCURATED",()),
        DesktopProject("physica","Physica","thebrazenbeard/physica","public","UNCURATED",()),
        DesktopProject("profile","Profile","thebrazenbeard/thebrazenbeard","public","UNCURATED",()),
    )
    # The explicit click cannot run on curated fallback state.
    app._qualify_live_portfolio()
    assert "Sync GitHub" in app.portfolio_action_var.message
    observed={}
    app._live_inventory_loaded=True
    def fake_qualify(owner,count,output,*,max_parallel):
        observed.update(owner=owner,count=count,output=output,max_parallel=max_parallel)
        return {"completed":2,"expected_count":2}
    monkeypatch.setattr("portal.desktop_app.qualify_portfolio",fake_qualify)
    app._start_portfolio_action=lambda title,callback:observed.update(
        title=title,result=callback()
    )
    app._qualify_live_portfolio()
    assert observed["owner"]=="thebrazenbeard"
    assert observed["count"]==2
    assert observed["max_parallel"]==8
    assert "0 projects executed" in observed["result"]
    assert str(observed["output"]).startswith(str(tmp_path/"state"/"portal"))


def test_first_live_default_branch_read_is_authenticated(
    monkeypatch,
) -> None:
    from portal import desktop_portfolio as mod
    from portal.desktop_portfolio import inspect_repository_head
    import json

    paths=[]
    def fake_run(cmd,**kwargs):
        paths.append(cmd)
        if len(paths)==1:
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "full_name":"thebrazenbeard/private-project",
                "default_branch":"production"
            }),stderr="")
        return SimpleNamespace(returncode=0,stdout=json.dumps({
            "commit":{"sha":"f"*40}
        }),stderr="")
    monkeypatch.setattr(mod.subprocess,"run",fake_run)
    assert inspect_repository_head("thebrazenbeard/private-project")=="f"*40
    assert paths[1]==[
        "gh","api","repos/thebrazenbeard/private-project/branches/production"
    ]
