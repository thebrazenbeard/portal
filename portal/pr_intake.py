"""Fail-closed, read-only GitHub PR intake for Portal's existing work scheduler.

Every open PR becomes one *candidate* exact-head review subject. This module
does not claim/dispatch workers, merge branches, set dispositions, or grant any
authority. Candidate records are observations, not durable execution leases.
Private inventory may only be written to an explicitly selected local path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from .discovery import GitHubRepositoryCatalog, RepositoryInventoryItem


SCHEMA = "PORTAL_PR_INTAKE_SNAPSHOT_V1"
CANDIDATE_SCHEMA = "PORTAL_PR_INTAKE_CANDIDATE_V1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FILENAME_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PAGES = 100


def _required_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA40.fullmatch(value) is None:
        raise ValueError(f"{label} must be exact lowercase sha40")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _safe_filename(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or _FILENAME_CONTROL.search(value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("PR changed filename is not a safe relative path")
    return value


def _changed_file_names(raw_files: Sequence[object]) -> tuple[str, ...]:
    names: set[str] = set()
    for raw in raw_files:
        item = _mapping(raw, "PR changed file")
        name = _safe_filename(item.get("filename"))
        if name in names:
            raise ValueError("duplicate changed file in PR")
        names.add(name)
        previous = item.get("previous_filename")
        if previous is not None:
            names.add(_safe_filename(previous))
    return tuple(sorted(names))


def pr_candidate(
    repository: RepositoryInventoryItem,
    raw_pull: object,
    raw_files: Sequence[object],
    *,
    verification: object,
) -> dict[str, object]:
    """Bind a PR to its exact observed revision, without authorizing effects.

    A second GET of the PR after the file listing must still match the first
    head/base. If it moved mid-scan, reject this observation and retry in a
    later independent run. Before a real worker claim, currentness must be
    rechecked again by the existing Project Runner exact-head path.
    """
    if not _REPO.fullmatch(repository.full_name):
        raise ValueError("repository name is invalid")
    pr = _mapping(raw_pull, "pull request")
    check = _mapping(verification, "verification PR")
    number = pr.get("number")
    if type(number) is not int or number <= 0:
        raise ValueError("pull request number must be positive integer")
    if pr.get("state") != "open" or check.get("state") != "open":
        raise ValueError("pull request is no longer open")
    if check.get("number") != number:
        raise ValueError("pull request identity changed during intake")
    initial_head = _mapping(pr.get("head"), "PR head")
    initial_base = _mapping(pr.get("base"), "PR base")
    final_head = _mapping(check.get("head"), "verification PR head")
    final_base = _mapping(check.get("base"), "verification PR base")
    head = _required_sha(initial_head.get("sha"), "PR head")
    base = _required_sha(initial_base.get("sha"), "PR base")
    if (
        _required_sha(final_head.get("sha"), "verification PR head") != head
        or _required_sha(final_base.get("sha"), "verification PR base") != base
    ):
        raise ValueError("pull request head/base changed during intake")
    target_repo = _mapping(initial_base.get("repo"), "PR base repository")
    verified_repo = _mapping(final_base.get("repo"), "verification base repository")
    if (
        str(target_repo.get("full_name", "")).casefold() != repository.full_name.casefold()
        or str(verified_repo.get("full_name", "")).casefold() != repository.full_name.casefold()
    ):
        raise ValueError("PR base repository crossed inventory boundary")
    if initial_base.get("ref") != final_base.get("ref"):
        raise ValueError("PR target ref changed during intake")
    if not isinstance(initial_base.get("ref"), str) or not initial_base["ref"]:
        raise ValueError("PR base ref is missing")
    initial_head_repo = initial_head.get("repo")
    final_head_repo = final_head.get("repo")
    head_repo = (
        _mapping(initial_head_repo, "PR head repository").get("full_name")
        if initial_head_repo is not None else None
    )
    final_head_repo_name = (
        _mapping(final_head_repo, "verification head repository").get("full_name")
        if final_head_repo is not None else None
    )
    if head_repo != final_head_repo_name or initial_head.get("ref") != final_head.get("ref"):
        raise ValueError("PR source identity changed during intake")
    if head_repo is not None and (
        not isinstance(head_repo, str) or _REPO.fullmatch(head_repo) is None
    ):
        raise ValueError("PR head repository invalid")
    if not isinstance(raw_files, (list, tuple)):
        raise ValueError("PR changed files must be a complete list")
    files = _changed_file_names(raw_files)
    repository_name = repository.full_name.lower()
    canonical = json.dumps(files, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    files_sha = hashlib.sha256(canonical).hexdigest()
    key = f"github-pr:{repository_name}#{number}"
    return {
        "schema": CANDIDATE_SCHEMA,
        "subject_key": key,
        "revision_key": f"{key}@{head}:{base}:{files_sha}",
        "repository": repository.full_name,
        "private": repository.private,
        "number": number,
        "head_sha": head,
        "base_sha": base,
        "head_repository": head_repo,
        "head_ref": initial_head.get("ref"),
        "base_ref": initial_base["ref"],
        "draft": bool(pr.get("draft", False)),
        "changed_files": list(files),
        "changed_files_sha256": files_sha,
        "review_collision_keys": [
            f"file:{repository_name}/{name.casefold()}" for name in files
        ],
        "mutation_collision_keys": [f"repository:{repository_name}"],
        "disposition": "UNREVIEWED",
        "candidate_dispositions": ["KEEP", "CONSOLIDATE", "SUPERSEDE"],
        "execution_authority": False,
        "source_mutation_authorized": False,
        "protected_effects_authorized": False,
        "currentness": "OBSERVED_ONLY_RECHECK_AT_CLAIM",
    }


class GitHubPullRequestCatalog(GitHubRepositoryCatalog):
    """Authenticated public/private GET-only REST adapter with full pagination."""

    def _pages(self, url: str) -> list[object]:
        result: list[object] = []
        for page in range(1, _MAX_PAGES + 1):
            separator = "&" if "?" in url else "?"
            payload = self._get_json(f"{url}{separator}per_page=100&page={page}")
            if not isinstance(payload, list):
                raise ValueError("GitHub paginated read was not a list")
            result.extend(payload)
            if len(payload) < 100:
                return result
        raise RuntimeError("GitHub pagination cap reached; refuse partial PR inventory")

    def list_open_pull_requests(self, repository: str) -> list[object]:
        if _REPO.fullmatch(repository) is None:
            raise ValueError("invalid repository for pull request query")
        return self._pages(f"{self.api_base}/repos/{repository}/pulls?state=open")

    def list_pr_files(self, repository: str, number: int) -> list[object]:
        if _REPO.fullmatch(repository) is None or type(number) is not int or number <= 0:
            raise ValueError("invalid PR file lookup")
        return self._pages(f"{self.api_base}/repos/{repository}/pulls/{number}/files")

    def get_pr(self, repository: str, number: int) -> object:
        if _REPO.fullmatch(repository) is None or type(number) is not int or number <= 0:
            raise ValueError("invalid PR verification lookup")
        return self._get_json(f"{self.api_base}/repos/{repository}/pulls/{number}")


def collect_pr_candidates(
    owner: str,
    repositories: Sequence[RepositoryInventoryItem],
    catalog: GitHubPullRequestCatalog,
    *,
    include_private: bool = False,
    observed_at: str | None = None,
) -> dict[str, object]:
    """Collect a complete local snapshot or raise; never publish partial output."""
    if not owner or not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise ValueError("invalid portfolio owner")
    if include_private and not catalog.token:
        raise ValueError("private PR intake requires authenticated GitHub read")
    seen_repos: set[str] = set()
    subjects: dict[str, dict[str, object]] = {}
    for repo in repositories:
        repo_key = repo.full_name.casefold()
        if repo_key in seen_repos:
            raise ValueError("duplicate repository in PR intake")
        seen_repos.add(repo_key)
        if repo.full_name.split("/")[0].casefold() != owner.casefold():
            raise ValueError("PR inventory crossed portfolio owner")
        if repo.archived or (repo.private and not include_private):
            continue
        for raw in catalog.list_open_pull_requests(repo.full_name):
            pr = _mapping(raw, "open pull request")
            number = pr.get("number")
            if type(number) is not int or number <= 0:
                raise ValueError("invalid PR number")
            files = catalog.list_pr_files(repo.full_name, number)
            checked = catalog.get_pr(repo.full_name, number)
            candidate = pr_candidate(repo, pr, files, verification=checked)
            key = str(candidate["subject_key"])
            if key in subjects:
                raise ValueError("duplicate PR identity across pagination")
            subjects[key] = candidate
    return {
        "schema": SCHEMA,
        "owner": owner,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "repository_inventory_count": len(seen_repos),
        "private_scope_included": include_private,
        "candidate_count": len(subjects),
        "candidates": [subjects[k] for k in sorted(subjects)],
        "live_execution": False,
        "source_mutation_authorized": False,
        "protected_effects_authorized": False,
        "transport": "GITHUB_REST_GET_ONLY",
        "completeness": "COMPLETE_OR_ERROR",
    }


def write_local_snapshot(path: Path, snapshot: Mapping[str, object]) -> None:
    """Create only; never replace an existing snapshot or publish it to Git."""
    target = Path(path).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    if target.is_relative_to(repo_root):
        raise ValueError("PR snapshots must not be written into Portal's source tree")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(dict(snapshot), stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portal read-only exact PR intake")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-private", action="store_true")
    parser.add_argument("--expected-repos", type=int)
    args = parser.parse_args(argv)
    token = os.environ.get("PORTAL_GITHUB_TOKEN") or os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    catalog = GitHubPullRequestCatalog(token=token)
    repositories = catalog.list_owned_repositories(args.owner)
    if args.expected_repos is not None and len(repositories) != args.expected_repos:
        parser.error("live repository count diverged; no partial output written")
    snapshot = collect_pr_candidates(
        args.owner, repositories, catalog,
        include_private=args.include_private,
    )
    write_local_snapshot(args.output, snapshot)
    print(json.dumps({
        "schema": SCHEMA,
        "repositories": len(repositories),
        "candidates": snapshot["candidate_count"],
        "private_included": args.include_private,
        "local_output_written": True,
        "protected_effects_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
