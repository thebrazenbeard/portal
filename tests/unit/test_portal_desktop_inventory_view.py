"""An owned GitHub portfolio is distinct from the executable wave."""
import json
from pathlib import Path
from portal.desktop_portfolio import DesktopPortfolioController
from portal.discovery import RepositoryInventoryItem
from portal.session import PortalCommandSession

def controller(tmp_path, monkeypatch, token="existing-auth"):
    source=tmp_path/"sources"/"portal"/"portfolio"
    source.mkdir(parents=True)
    (source/"advancement_wave.public.json").write_text(json.dumps({
        "items":[{"repository":"thebrazenbeard/firesafe"}]}))
    session=PortalCommandSession(tmp_path/"state"/"portal"/"session.sqlite3")
    ctl=DesktopPortfolioController(tmp_path,session=session,
                                   token_provider=lambda: token)
    class FakeGitHub:
        def __init__(self, *, token):
            assert token=="existing-auth"
        def list_owned_repositories(self, owner):
            assert owner=="thebrazenbeard"
            return (RepositoryInventoryItem("blotter","thebrazenbeard/blotter",False,False,"main"),
                    RepositoryInventoryItem("vera-os","thebrazenbeard/vera-os",True,False,"main"),
                    RepositoryInventoryItem("old","thebrazenbeard/old",False,True,"main"))
    monkeypatch.setattr("portal.desktop_portfolio.GitHubRepositoryCatalog", FakeGitHub)
    return ctl,session

def test_authenticated_inventory_lists_all_owned_repos_not_only_wave(tmp_path,monkeypatch):
    ctl,session=controller(tmp_path,monkeypatch)
    try:
        result=ctl.handle({"action":"inventory","session_id":"portfolio"})
        assert [(x["name"],x["visibility"],x["archived"])
                for x in result["repositories"]]==[
            ("blotter","PUBLIC",False),("vera-os","PRIVATE",False),("old","PUBLIC",True)]
        assert result["inventory"]=={"public":2,"private":1,"archived":1}
        assert result["protected_effect_authority"] is False
        assert result["portfolio_source"]=="LIVE_GITHUB"
        assert result["session"] is None
    finally: session.close()

def test_inventory_without_authentication_fails_closed(tmp_path,monkeypatch):
    ctl,session=controller(tmp_path,monkeypatch,token=None)
    try:
        import pytest
        with pytest.raises(ValueError,match="authenticated"):
            ctl.handle({"action":"inventory"})
    finally: session.close()

def test_older_installed_resident_uses_explicit_readonly_frontend_fallback(
    tmp_path, monkeypatch
):
    from portal.desktop_app import DesktopViewModel
    from portal.discovery import RepositoryInventoryItem
    wave=tmp_path/"wave.json"
    wave.write_text(json.dumps({"items":[{"repository":"thebrazenbeard/firesafe"}]}))
    calls=[]
    class IPC:
        def request(self, command, **kwargs):
            calls.append((command,kwargs))
            if kwargs.get("action")=="inventory":
                raise RuntimeError("ValueError: unsupported portfolio control")
            return {"profile":{"wave":str(wave),"discover_owner":None}}
    class Catalog:
        def __init__(self,*,token):
            assert token=="valid-token"
        def list_owned_repositories(self,owner):
            assert owner=="thebrazenbeard"
            return (RepositoryInventoryItem("private","thebrazenbeard/private",True,False,"main"),)
    monkeypatch.setattr("portal.desktop_app.GitHubRepositoryCatalog",Catalog)
    monkeypatch.setattr("portal.desktop_app._existing_github_token",
                        lambda:"valid-token")
    result=DesktopViewModel(IPC()).owned_inventory()
    assert result["protected_effect_authority"] is False
    assert result["portfolio_source"]=="LIVE_GITHUB_READ_ONLY_FRONTEND"
    assert result["repositories"][0]["visibility"]=="PRIVATE"
    assert [payload["action"] for _,payload in calls]==["status"]


def test_inventory_view_displays_private_and_archived_status_without_queue():
    from portal.desktop_app import PortalDesktopApp
    class Tree:
        rows=[]
        def get_children(self): return ()
        def delete(self,*_args): pass
        def insert(self,*_args,**kwargs): self.rows.append(kwargs["values"])
    class Label:
        value=""
        def set(self,text):self.value=text
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app.repository_tree=Tree()
    app.inventory_var=Label()
    app._render_inventory({"portfolio_source":"LIVE_GITHUB",
        "repositories":[
          {"full_name":"thebrazenbeard/portal","visibility":"PUBLIC",
           "archived":False,"default_branch":"main"},
          {"full_name":"thebrazenbeard/vera-os","visibility":"PRIVATE",
           "archived":True,"default_branch":"main"}]})
    assert len(app.repository_tree.rows)==2
    assert app.repository_tree.rows[1][1:3]==("PRIVATE","ARCHIVED")
    assert "2 owned repositories" in app.inventory_var.value

def test_ollama_reply_is_not_misattributed_to_vera_identity():
    from portal.desktop_app import PortalDesktopApp
    collected=[]
    class Label:
        def set(self,*args):pass
    class Button:
        def configure(self,**kwargs):pass
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app._append_conversation=lambda speaker,text:collected.append((speaker,text))
    app.status_var=Label()
    app.send_button=Button()
    app.refresh_async=lambda:None
    app._render_response({"response_text":"I am Qwen.",
        "provider":"ollama","model_or_agent":"vera-local:latest",
        "state":"COMPLETED"})
    assert collected[0][0]=="Local Ollama model (vera-local:latest)"
    assert collected[0][1]=="I am Qwen."
    assert collected[1][0]=="Provenance"

def test_local_proposal_view_reads_only_owned_job_artifact(tmp_path):
    from types import SimpleNamespace
    from portal.desktop_app import PortalDesktopApp
    path=tmp_path/"proposal.json"
    path.write_text(json.dumps({
        "schema":"PORTAL_SOURCE_TREE_PROPOSAL_V1",
        "repository":"thebrazenbeard/portal","expected_head":"a"*40,
        "files":[{"content":"Source-only observations"}]}))
    class Tree:
        def selection(self):return ("job1",)
    class Label:
        last=""
        def set(self,x):self.last=x
    class Notebook:
        selected=None
        def select(self,tab):self.selected=tab
    app=PortalDesktopApp.__new__(PortalDesktopApp)
    app.local_jobs_tree=Tree()
    app._repo_jobs=SimpleNamespace(root=tmp_path)
    app._repo_job_rows={"job1":{"state":"AWAITING_REVIEW",
        "repository":"thebrazenbeard/portal","head":"a"*40,
        "proposal_path":str(path)}}
    app.portfolio_var=Label()
    app._inventory_notebook=Notebook()
    app._conversation_tab="conversation"
    messages=[]
    app._append_conversation=lambda who,content:messages.append((who,content))
    app.view_selected_local_proposal()
    assert messages[0][1]=="Source-only observations"
    assert app._inventory_notebook.selected=="conversation"
    app._repo_job_rows["job1"]["proposal_path"]=str(tmp_path.parent/"outside.json")
    app.view_selected_local_proposal()
    assert "outside" in app.portfolio_var.last
    assert len(messages)==1
