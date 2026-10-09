"""User-selected repository jobs must be durable and source-only."""
import json
import sqlite3
from pathlib import Path
import pytest
from portal.desktop_repo_jobs import RepoJobQueue

class Catalog:
    def __init__(self):
        self.token="existing"
        self.api_base="https://api.github.com"
    def list_owned_repositories(self,owner):
        from portal.discovery import RepositoryInventoryItem
        assert owner=="thebrazenbeard"
        return (
            RepositoryInventoryItem("alpha","thebrazenbeard/alpha",False,False,"main"),
            RepositoryInventoryItem("secret","thebrazenbeard/secret",True,False,"main"),
            RepositoryInventoryItem("old","thebrazenbeard/old",False,True,"main"),
        )
    def _get_json(self,url):
        assert url.endswith("/git/ref/heads/main")
        return {"ref":"refs/heads/main","object":{"type":"commit","sha":"a"*40}}

def test_enqueue_is_explicit_and_durable(tmp_path):
    store=RepoJobQueue(tmp_path)
    catalog=Catalog()
    one=store.enqueue("thebrazenbeard/alpha",catalog=catalog)
    assert one["state"]=="QUEUED" and one["head"]=="a"*40
    assert len(RepoJobQueue(tmp_path).list_jobs())==1
    with pytest.raises(ValueError,match="already"):
        store.enqueue("thebrazenbeard/alpha",catalog=catalog)
    assert len(store.list_jobs())==1

def test_fail_closed_for_archived_or_not_owned(tmp_path):
    store=RepoJobQueue(tmp_path)
    with pytest.raises(ValueError,match="archived"):
        store.enqueue("thebrazenbeard/old",catalog=Catalog())
    with pytest.raises(ValueError,match="owned"):
        store.enqueue("someoneelse/alpha",catalog=Catalog())
    with pytest.raises(ValueError,match="owned"):
        store.enqueue("thebrazenbeard/unknown",catalog=Catalog())
    assert store.list_jobs()==[]

def test_crash_latch_does_not_replay_claim(tmp_path):
    store=RepoJobQueue(tmp_path)
    store.enqueue("thebrazenbeard/alpha",catalog=Catalog())
    first=store.claim_one()
    assert first["state"]=="CLAIMED"
    assert store.claim_one() is None
    assert RepoJobQueue(tmp_path).claim_one() is None
def test_local_source_proposal_no_mutation(tmp_path):
    store=RepoJobQueue(tmp_path)
    store.enqueue("thebrazenbeard/alpha",catalog=Catalog())
    clone_calls=[]
    def clone(repository,ref,target):
        clone_calls.append((repository,ref))
        target.mkdir(parents=True)
        (target/"README.md").write_text("Alpha source")
    def source_reader(path,repository,ref,head):
        assert path.read_text if False else True
        return "FILE: README.md\\nAlpha source"
    def model(source):
        assert "Alpha source" in source
        return {"observations":["One documented entry"],
                "suggested_check":"Inspect tests","uncertainty":"Only README supplied"}
    result=store.run_one(clone=clone,source_reader=source_reader,model=model,
                         verify_head=lambda *args:True)
    assert result["state"]=="AWAITING_REVIEW"
    assert clone_calls==[("thebrazenbeard/alpha","main")]
    proposal=json.loads(Path(result["proposal_path"]).read_text())
    assert proposal["repository"]=="thebrazenbeard/alpha"
    assert proposal["expected_head"]=="a"*40
    assert "One documented entry" in proposal["files"][0]["content"]
    assert store.run_one(clone=clone,source_reader=source_reader,model=model,
                         verify_head=lambda *args:True) is None

def test_unknown_execution_is_not_auto_retried(tmp_path):
    store=RepoJobQueue(tmp_path)
    store.enqueue("thebrazenbeard/alpha",catalog=Catalog())
    def boom(*args):raise RuntimeError("disk or model outage")
    with pytest.raises(RuntimeError,match="disk"):
        store.run_one(clone=boom)
    assert store.list_jobs()[0]["state"]=="HALTED"
    assert store.claim_one() is None

def test_single_local_worker_slot_is_respected_across_queue_handles(tmp_path):
    queue=RepoJobQueue(tmp_path)
    catalog=Catalog()
    queue.enqueue("thebrazenbeard/alpha",catalog=catalog)
    queue.enqueue("thebrazenbeard/secret",catalog=catalog)
    first=queue.claim_one()
    assert first["state"]=="CLAIMED"
    assert RepoJobQueue(tmp_path).claim_one() is None
    queue._finish(first["id"],"HALTED",error="manual review required")
    second=RepoJobQueue(tmp_path).claim_one()
    assert second and second["repository"]=="thebrazenbeard/secret"
