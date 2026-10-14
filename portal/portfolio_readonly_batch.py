"""Read-only, exact-scope owner portfolio qualification in bounded parallel waves.

This performs authenticated GitHub metadata reads, not project code execution.
No workflow dispatch, repository write, merge, install, credential or paid effect.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

SCHEMA = "PORTAL_PORTFOLIO_READONLY_BATCH_V1"
MAX_PARALLEL = 8
_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_OWNER = re.compile(r"[A-Za-z0-9-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("x", encoding="utf-8") as file:
        json.dump(value, file, sort_keys=True, indent=2)
        file.write("\n")
    os.replace(tmp, path)


def github_read(args: list[str]) -> Any:
    """Invoke only the authenticated read-only GitHub CLI endpoints."""
    if not args or args[0] not in ("repo", "api"):
        raise ValueError("only GitHub repository inventory/API reads permitted")
    if args[0] == "repo":
        if (
            len(args) != 7 or args[1] != "list"
            or not _OWNER.fullmatch(args[2])
            or args[3:] != [
                "--limit", "300", "--json",
                "nameWithOwner,isArchived,isPrivate",
            ]
        ):
            raise ValueError("unexpected GitHub inventory command")
    if args[0] == "api" and (
        len(args) != 2
        or not args[1].startswith("repos/")
        or any(c in args[1] for c in "\r\n\t ")
    ):
        raise ValueError("unexpected GitHub API request")
    outcome = subprocess.run(
        ["gh", *args], capture_output=True, text=True, shell=False,
        timeout=20, check=False,
    )
    if outcome.returncode != 0:
        raise RuntimeError(
            "GitHub read unavailable: "
            + (outcome.stderr or outcome.stdout).strip()[:180]
        )
    return json.loads(outcome.stdout)


def owner_scope(
    owner: str, *, expected_count: int, reader: Callable[[list[str]], Any] = github_read,
) -> tuple[str, ...]:
    if not _OWNER.fullmatch(owner):
        raise ValueError("invalid GitHub owner")
    if type(expected_count) is not int or not 1 <= expected_count <= 300:
        raise ValueError("expected scope must be 1..300")
    source = reader([
        "repo", "list", owner, "--limit", "300",
        "--json", "nameWithOwner,isArchived,isPrivate",
    ])
    if not isinstance(source, list):
        raise ValueError("GitHub inventory is not a list")
    all_names: set[str] = set()
    selected: list[str] = []
    for record in source:
        if not isinstance(record, dict):
            raise ValueError("GitHub inventory contains malformed entry")
        name = record.get("nameWithOwner")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("GitHub repository name invalid")
        if name.split("/")[0].casefold() != owner.casefold():
            raise ValueError("GitHub inventory crossed owner boundary")
        if type(record.get("isArchived")) is not bool:
            raise ValueError("GitHub archive field absent")
        if type(record.get("isPrivate")) is not bool:
            raise ValueError("GitHub visibility field absent")
        key = name.casefold()
        if key in all_names:
            raise ValueError("duplicate repo in inventory")
        all_names.add(key)
        if not record["isArchived"] and key != (owner+"/"+owner).casefold():
            selected.append(name)
    if len(selected) != expected_count:
        raise RuntimeError(
            f"requested {expected_count} projects, live scope is {len(selected)}; "
            "no implicit expansion or truncation"
        )
    return tuple(sorted(selected, key=str.casefold))


def _qualified_read(
    repository: str, reader: Callable[[list[str]], Any],
) -> dict[str, object]:
    """One independent repository-specific bounded assessment."""
    details = reader(["api", f"repos/{repository}"])
    if not isinstance(details, dict) or details.get("full_name", "").casefold() != repository.casefold():
        raise RuntimeError("GitHub repository identity mismatch")
    branch = details.get("default_branch")
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,100}", branch):
        raise RuntimeError("unqualified default branch")
    head = reader(["api", f"repos/{repository}/branches/{branch}"])
    sha = head.get("commit", {}).get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{40}", sha):
        raise RuntimeError("unqualified exact main head")
    root = reader(["api", f"repos/{repository}/git/trees/{branch}"])
    if not isinstance(root, dict) or not isinstance(root.get("tree"), list):
        raise RuntimeError("repository tree unavailable")
    names = [e["path"] for e in root["tree"]
             if isinstance(e, dict) and isinstance(e.get("path"), str)]
    prs = reader(["api", f"repos/{repository}/pulls?state=open&per_page=100"])
    if not isinstance(prs, list):
        raise RuntimeError("open PR query invalid")
    ci = reader(["api", f"repos/{repository}/actions/runs?branch={branch}&per_page=1"])
    recent = ci.get("workflow_runs", []) if isinstance(ci, dict) else []
    latest_ci = recent[0] if recent else {}
    category = (
        "REVIEW_EXISTING_PR" if prs else
        "TRIAGE_FAILING_CI" if latest_ci.get("conclusion") == "failure" else
        "QUALIFY_CI_COVERAGE" if ".github" not in names else
        "IDENTIFY_VERIFICATION_SUITE" if not any(n in names for n in ("tests", "test", "spec", "specs")) else
        "BIND_EXECUTION_TARGET" if not any(n in names for n in
            ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml", "requirements.txt")) else
        "QUALIFY_SAFE_WORKER_ROUTE"
    )
    return {
        "repository":repository, "default_branch":branch, "head_sha":sha,
        "private":bool(details.get("private")), "open_pr_count":len(prs),
        "first_open_pr":(prs[0].get("number") if prs else None),
        "root_entry_count":len(names),
        "recent_ci":{
            "head_sha":latest_ci.get("head_sha"),
            "conclusion":latest_ci.get("conclusion"),
            "status":latest_ci.get("status"),
        },
        "next_action_category":category,
        "code_executed":False,
        "protected_effects_performed":False,
        "exact_head_source":"authenticated GitHub API",
    }


def qualify_portfolio(
    owner: str, expected_count: int, output: Path, *,
    max_parallel: int = 8,
    reader: Callable[[list[str]], Any] = github_read,
) -> dict[str, object]:
    """Persist one scope receipt plus one durable attempted/final per subject.

    Does not retry indeterminate worker outcomes or alter GitHub.
    """
    if type(max_parallel) is not int or not 1 <= max_parallel <= MAX_PARALLEL:
        raise ValueError("parallelism must be 1..8")
    targets = owner_scope(owner, expected_count=expected_count, reader=reader)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    _atomic(output / "scope.json", {
        "schema":SCHEMA, "owner":owner, "selected_repositories":targets,
        "selected_count":len(targets), "max_parallel":max_parallel,
        "effect_ceiling":"GITHUB_METADATA_READ_ONLY", "started_at":_now(),
    })
    def work(name: str) -> dict[str, object]:
        key = name.split("/")[1].casefold()
        directory = output / "subjects" / key
        _atomic(directory / "attempted.json", {
            "schema":SCHEMA, "repository":name,
            "state":"ATTEMPTED", "started_at":_now(),
            "automatic_retry":False,
        })
        try:
            value = _qualified_read(name, reader)
            state = "COMPLETE"
        except Exception as exc:
            value = {"error":f"{type(exc).__name__}: {exc}"[:250]}
            state = "HELD"
        result={
            "schema":SCHEMA, "repository":name,
            "state":state, "completed_at":_now(),
            "read_only":True, "automatic_retry":False, **value,
        }
        _atomic(directory / "final.json", result)
        return result
    finished=[]
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=max_parallel,
                            thread_name_prefix="portal-portfolio-read") as pool:
        submitted={pool.submit(work,name):name for name in targets}
        for item in as_completed(submitted):
            finished.append(item.result())
    counts=dict(Counter(item["state"] for item in finished))
    categories=dict(Counter(item.get("next_action_category","UNRESOLVED")
                            for item in finished))
    summary={
        "schema":SCHEMA, "expected_count":expected_count,
        "completed":len(finished), "counts":counts, "categories":categories,
        "parallel_limit":max_parallel,
        "duration_seconds":round(time.monotonic()-started,2),
        "repository_code_executed":False,
        "git_provider_mutations":False,
        "projects_executed":0,
        "work_completed":"READ_ONLY_QUALIFICATION_NOT_PROJECT_EXECUTION",
    }
    _atomic(output / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner",default="thebrazenbeard")
    parser.add_argument("--count",type=int,required=True)
    parser.add_argument("--max-parallel",type=int,default=8)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    result=qualify_portfolio(
        args.owner, args.count, args.output, max_parallel=args.max_parallel)
    print(json.dumps(result,sort_keys=True))
    return 0 if result["counts"].get("COMPLETE")==result["expected_count"] else 1


if __name__=="__main__":
    raise SystemExit(main())
