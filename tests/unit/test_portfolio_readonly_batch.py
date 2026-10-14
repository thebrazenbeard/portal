from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from portal.portfolio_readonly_batch import (
    MAX_PARALLEL,
    _qualified_read,
    github_read,
    owner_scope,
    qualify_portfolio,
)


def fake_reader(repos: list[str], *, fail: str | None = None):
    lock=threading.Lock()
    inflight=0
    peak=0
    executed=[]

    def reader(args: list[str]):
        nonlocal inflight, peak
        with lock:
            inflight+=1
            peak=max(peak,inflight)
            executed.append(tuple(args))
        try:
            time.sleep(.001)
            if args[:3] == ["repo","list","thebrazenbeard"]:
                return [
                    {"nameWithOwner":repo, "isArchived":False,"isPrivate":False}
                    for repo in repos
                ] + [
                    {"nameWithOwner":"thebrazenbeard/thebrazenbeard",
                     "isArchived":False,"isPrivate":False},
                    {"nameWithOwner":"thebrazenbeard/dead",
                     "isArchived":True,"isPrivate":False},
                ]
            assert args[0] == "api"
            path=args[1]
            name=path.split("/")[1]+"/"+path.split("/")[2]
            if name==fail:raise RuntimeError("read unqualified")
            if path == "repos/"+name:
                return {
                    "full_name":name,"default_branch":"main","private":False,
                }
            if path.endswith("/branches/main"):
                return {"commit":{"sha":"a"*40}}
            if path.endswith("/git/trees/main"):
                return {"tree":[{"path":p} for p in
                        ("README.md", ".github", "tests", "pyproject.toml")]}
            if "/pulls?" in path:return []
            if "/actions/runs?" in path:return {
                "workflow_runs":[{"status":"completed","conclusion":"success",
                                  "head_sha":"a"*40}],
            }
            raise AssertionError(args)
        finally:
            with lock:inflight-=1

    def stats():
        return {"peak":peak, "calls":len(executed),"inflight":inflight}
    return reader,stats


def test_exact_89_owner_scope_excludes_profile_and_archived_and_fails_on_drift():
    repos=[f"thebrazenbeard/project-{i:03d}" for i in range(89)]
    reader,_=fake_reader(repos)
    names=owner_scope("thebrazenbeard",expected_count=89,reader=reader)
    assert len(names)==89
    assert "thebrazenbeard/thebrazenbeard" not in names
    assert "thebrazenbeard/dead" not in names
    with pytest.raises(RuntimeError,match="live scope is 89"):
        owner_scope("thebrazenbeard",expected_count=88,reader=reader)
    with pytest.raises(ValueError):
        owner_scope("thebrazenbeard/../evil",expected_count=89,reader=reader)


def test_parallel_89_source_reads_persist_exact_attempts_and_finals(tmp_path: Path):
    repos=[f"thebrazenbeard/project-{i:03d}" for i in range(89)]
    reader,stats=fake_reader(repos)
    output=tmp_path/"batch"
    summary=qualify_portfolio(
        "thebrazenbeard",89,output,max_parallel=8,reader=reader)
    assert summary["completed"]==89
    assert summary["counts"]=={"COMPLETE":89}
    assert summary["projects_executed"]==0
    assert summary["repository_code_executed"] is False
    assert summary["parallel_limit"]==8
    assert stats()["peak"]>=2
    assert stats()["peak"]<=MAX_PARALLEL
    assert stats()["inflight"]==0
    receipts=list((output/"subjects").glob("*/final.json"))
    assert len(receipts)==89
    for receipt in receipts:
        data=json.loads(receipt.read_text())
        assert data["state"]=="COMPLETE"
        assert data["head_sha"]=="a"*40
        assert data["next_action_category"]=="QUALIFY_SAFE_WORKER_ROUTE"
        attempted=json.loads((receipt.parent/"attempted.json").read_text())
        assert attempted["state"]=="ATTEMPTED"
    with pytest.raises(FileExistsError):
        qualify_portfolio("thebrazenbeard",89,output,reader=reader)


def test_partial_failure_is_held_not_fake_completed_or_retried(tmp_path: Path):
    repos=[f"thebrazenbeard/project-{i:03d}" for i in range(3)]
    reader,stats=fake_reader(repos,fail=repos[1])
    summary=qualify_portfolio("thebrazenbeard",3,tmp_path/"batch",
                             max_parallel=3,reader=reader)
    assert summary["counts"]=={"COMPLETE":2,"HELD":1}
    failed=json.loads((tmp_path/"batch"/"subjects"/"project-001"/"final.json").read_text())
    assert failed["state"]=="HELD"
    assert failed["automatic_retry"] is False
    assert stats()["inflight"]==0


def test_scope_change_rejected_before_any_local_state_created(tmp_path: Path):
    reader,_=fake_reader(["thebrazenbeard/project-one"])
    with pytest.raises(RuntimeError):
        qualify_portfolio("thebrazenbeard",89,tmp_path/"must-not-exist",
                         reader=reader)
    assert not (tmp_path/"must-not-exist").exists()


def test_github_reader_rejects_mutation_commands():
    for args in (["repo","delete","thebrazenbeard/a"],
                 ["api","--method","PATCH","repos/thebrazenbeard/a"],
                 ["pr","merge","9"]):
        with pytest.raises((ValueError, RuntimeError)):
            github_read(args)
