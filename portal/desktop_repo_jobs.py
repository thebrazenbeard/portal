"""Durable user-selected, source-only local proposal jobs.

This operates outside the resident command session. Every job is a one-shot
isolated checkout + local Ollama read, NOT resident worker admission.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid

_REPO=re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA=re.compile(r"^[0-9a-f]{40}$")
_SCHEMA="""
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, repository TEXT NOT NULL,
 source_ref TEXT NOT NULL, head TEXT NOT NULL,
 state TEXT NOT NULL, proposal_path TEXT, error TEXT,
 UNIQUE(repository,head)
);
"""

class RepoJobQueue:
    def __init__(self, root: Path):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/"selected-repository-jobs.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.executescript(_SCHEMA)

    def list_jobs(self)->list[dict]:
        with sqlite3.connect(self.db) as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY rowid DESC").fetchall()]

    def enqueue(self, repository: str, *, catalog)->dict:
        if not isinstance(repository,str) or not _REPO.fullmatch(repository):
            raise ValueError("repository is not a valid owner/name")
        owner, name=repository.split("/",1)
        if owner.casefold()!="thebrazenbeard":
            raise ValueError("repository is not owned by the configured portfolio")
        if not isinstance(getattr(catalog,"token",None),str) or not catalog.token.strip():
            raise ValueError("authenticated GitHub ownership required")
        matches=[r for r in catalog.list_owned_repositories(owner)
                 if r.full_name.casefold()==repository.casefold()]
        if len(matches)!=1:
            raise ValueError("repository is not in authenticated owned inventory")
        repo=matches[0]
        if repo.archived:
            raise ValueError("archived repository cannot be queued")
        if repo.full_name!=repository:
            raise ValueError("repository spelling differs from authenticated inventory")
        from urllib.parse import quote
        ref=repo.default_branch
        url=(f"{catalog.api_base}/repos/{quote(owner,safe='')}/"
             f"{quote(name,safe='')}/git/ref/heads/{quote(ref,safe='')}")
        data=catalog._get_json(url)
        obj=data.get("object") if isinstance(data,dict) else None
        if (data.get("ref")!=f"refs/heads/{ref}"
            or not isinstance(obj,dict) or obj.get("type")!="commit"
            or not isinstance(obj.get("sha"),str)
            or not _SHA.fullmatch(obj["sha"])):
            raise ValueError("authenticated GitHub default head unverified")
        head=obj["sha"]
        job_id=uuid.uuid4().hex
        try:
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO jobs(id,repository,source_ref,head,state)"
                    " VALUES(?,?,?,?,?)",(job_id,repository,ref,head,"QUEUED"))
        except sqlite3.IntegrityError as exc:
            raise ValueError("repository at this exact head is already queued or reviewed") from exc
        return self._get(job_id)

    def _get(self,job_id:str)->dict:
        with sqlite3.connect(self.db) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
            if row is None: raise ValueError("job missing")
            return dict(row)

    def claim_one(self)->dict|None:
        with sqlite3.connect(self.db) as db:
            db.execute("BEGIN IMMEDIATE")
            # One physical local-model lane across every preview/window.
            if db.execute("SELECT 1 FROM jobs WHERE state='CLAIMED'"
                          " LIMIT 1").fetchone() is not None:
                return None
            row=db.execute("SELECT id FROM jobs WHERE state='QUEUED'"
                           " ORDER BY rowid LIMIT 1").fetchone()
            if row is None:return None
            db.execute("UPDATE jobs SET state='CLAIMED' WHERE id=?",(row[0],))
        return self._get(row[0])

    def _finish(self, job_id:str, state:str, *, proposal:str|None=None,
                error:str|None=None)->dict:
        with sqlite3.connect(self.db) as db:
            count=db.execute("UPDATE jobs SET state=?,proposal_path=?,error=?"
                     " WHERE id=? AND state='CLAIMED'",
                     (state,proposal,error,job_id)).rowcount
            if count!=1:raise ValueError("job fence lost; do not retry")
        return self._get(job_id)
    @staticmethod
    def _clone(repository:str,ref:str,target:Path)->None:
        # GitHub CLI uses existing authorization; no token enters model process.
        cmd=["gh","repo","clone",repository,str(target),"--",
             "--depth","1","--single-branch","--branch",ref]
        completed=subprocess.run(cmd,check=False,capture_output=True,text=True,
                                 timeout=120)
        if completed.returncode:
            raise ValueError("authenticated repository checkout failed")

    @staticmethod
    def _verify(checkout:Path, repository:str, ref:str, head:str, catalog)->bool:
        rev=subprocess.run(["git","-C",str(checkout),"rev-parse","HEAD"],
            capture_output=True,text=True,check=True,timeout=20).stdout.strip()
        if rev!=head:return False
        from urllib.parse import quote
        owner,name=repository.split("/",1)
        url=(f"{catalog.api_base}/repos/{quote(owner,safe='')}/"
             f"{quote(name,safe='')}/git/ref/heads/{quote(ref,safe='')}")
        live=catalog._get_json(url)
        return (isinstance(live,dict) and live.get("ref")==f"refs/heads/{ref}"
                and isinstance(live.get("object"),dict)
                and live["object"].get("type")=="commit"
                and live["object"].get("sha")==head)

    @staticmethod
    def _source(checkout:Path, repository:str, ref:str, head:str)->str:
        from .local_ollama_worker import _validate
        packet={"schema":"PORTAL_WAVE_WORK_PACKET_V1",
           "advisory_only":True,"execution_authorized":False,
           "protected_effects_authorized":False,
           "source_mutation_authorized":False,"target_ref_mutation_authorized":False,
           "effect_ceiling":"SOURCE_ONLY",
           "execution_promotion":None,"execution_effect_class":None,
           "repository":repository,"source_ref":ref,"exact_head":head}
        return _validate(packet,checkout)

    @staticmethod
    def _model(source:str)->dict:
        from .local_ollama_worker import _query_local_model
        return _query_local_model(source,"vera-local:latest")

    def run_one(self, *, catalog=None, clone=None, source_reader=None,
                model=None, verify_head=None)->dict|None:
        job=self.claim_one()
        if job is None:return None
        job_dir=self.root/"jobs"/job["id"]
        # A claimed job is deliberately never retried automatically.
        try:
            if job_dir.exists():raise ValueError("job workspace collision")
            job_dir.mkdir(parents=True)
            checkout=job_dir/"checkout"
            (clone or self._clone)(job["repository"],job["source_ref"],checkout)
            verify=verify_head or (
                lambda p,r,ref,h:self._verify(p,r,ref,h,catalog))
            if not verify(checkout,job["repository"],job["source_ref"],job["head"]):
                raise ValueError("checkout no longer matches authenticated exact head")
            source=(source_reader or self._source)(
                checkout,job["repository"],job["source_ref"],job["head"])
            review=(model or self._model)(source)
            if (not isinstance(review,dict)
                or not isinstance(review.get("observations"),list)
                or not all(isinstance(x,str) and x for x in review["observations"])
                or not all(isinstance(review.get(k),str) and review[k]
                           for k in ("suggested_check","uncertainty"))):
                raise ValueError("local model returned unqualified proposal")
            content=("# Local model source inspection (unverified)\n\n"
              f"Repository: {job['repository']}\nExact head: {job['head']}\n"
              f"Evidence SHA-256: {hashlib.sha256(source.encode()).hexdigest()}\n"
              "Model: vera-local:latest (local Ollama)\n\n"
              "## Observations\n"+''.join("- "+x+"\n" for x in review["observations"])
              +"\n## Suggested verification\n"+review["suggested_check"]
              +"\n\n## Uncertainty\n"+review["uncertainty"]
              +"\n\nSource-only proposal. No source, GitHub or protected effects.\n")
            proposal={"schema":"PORTAL_SOURCE_TREE_PROPOSAL_V1",
             "repository":job["repository"],"source_ref":job["source_ref"],
             "expected_head":job["head"],"message":"Local advisory review; not published",
             "files":[{"path":"docs/portal/local-review-evidence-v1.md",
                       "content":content,"expected_blob_sha":None}]}
            path=job_dir/"proposal.json"
            with path.open("x",encoding="utf-8") as f:
                json.dump(proposal,f,indent=2)
                f.flush();os.fsync(f.fileno())
            return self._finish(job["id"],"AWAITING_REVIEW",proposal=str(path))
        except Exception as exc:
            self._finish(job["id"],"HALTED",
                         error=f"{type(exc).__name__}: {exc}"[:350])
            raise
