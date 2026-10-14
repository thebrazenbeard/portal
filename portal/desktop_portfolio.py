"""Read-only project inventory and bounded PORTAL orchestration entrypoints.

The curated project registry is not presented as a live GitHub membership scan.
No CLI command from this module grants write, merge, install, or paid authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class DesktopProject:
    id: str
    name: str
    repository: str
    visibility: str
    scheduling_state: str
    targets: tuple[str, ...]


def load_curated_portfolio(source_root: Path) -> tuple[DesktopProject, ...]:
    from runner.registry import load_project_snapshot

    source_root = Path(source_root).resolve()
    snapshot = load_project_snapshot(source_root / "registry" / "projects.yaml")
    records: list[DesktopProject] = []
    for item in snapshot.projects:
        for repository in item.repositories:
            if not _REPO.fullmatch(repository):
                raise ValueError(f"invalid repository locator: {repository!r}")
            records.append(
                DesktopProject(
                    id=item.id,
                    name=item.name,
                    repository=repository,
                    visibility=item.visibility,
                    scheduling_state=item.scheduling_state.value,
                    targets=tuple(
                        target.work_type
                        for target in item.execution_targets
                        if target.repository == repository
                    ),
                )
            )
    return tuple(sorted(records, key=lambda x: (x.name.casefold(), x.repository)))


def discover_github_portfolio(
    source_root: Path, *, owner: str = "thebrazenbeard",
    timeout_seconds: float = 45.0,
) -> tuple[DesktopProject, ...]:
    """Read the authenticated owner's live nonarchived GitHub repositories.

    Uses the local GitHub CLI authentication. Does not mutate source, secrets,
    or repository membership. Uncurated repositories have NO execution target.
    """
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise ValueError("invalid GitHub owner")
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    result = subprocess.run(
        [
            "gh", "repo", "list", owner, "--limit", "300",
            "--json", "nameWithOwner,isArchived,isPrivate",
        ],
        capture_output=True, text=True, timeout=timeout_seconds, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "authenticated GitHub inventory unavailable: "
            + result.stderr.strip()[:300]
        )
    raw = json.loads(result.stdout)
    if not isinstance(raw, list):
        raise ValueError("GitHub inventory is not a list")
    curated = {
        item.repository.casefold(): item
        for item in load_curated_portfolio(source_root)
    }
    projects: list[DesktopProject] = []
    seen: set[str] = set()
    for record in raw:
        if not isinstance(record, dict):
            raise ValueError("invalid GitHub repository record")
        name = record.get("nameWithOwner")
        if not isinstance(name, str) or not _REPO.fullmatch(name):
            raise ValueError("invalid GitHub repository locator")
        if name.split("/")[0].casefold() != owner.casefold():
            raise ValueError("GitHub inventory contains an external owner")
        key = name.casefold()
        if key in seen:
            raise ValueError("duplicate GitHub repository")
        seen.add(key)
        if record.get("isArchived") is True:
            continue
        if type(record.get("isArchived")) is not bool or type(record.get("isPrivate")) is not bool:
            raise ValueError("missing current archive/visibility fields")
        item = curated.get(key)
        projects.append(
            DesktopProject(
                id=item.id if item else name.split("/")[1].casefold(),
                name=item.name if item else name.split("/")[1],
                repository=name,
                visibility="private" if record["isPrivate"] else "public",
                scheduling_state=item.scheduling_state if item else "UNCURATED",
                targets=item.targets if item else (),
            )
        )
    return tuple(sorted(projects, key=lambda x: (x.name.casefold(), x.repository)))


def inspect_repository_head(
    repository: str, *, timeout_seconds: float = 15.0,
) -> str:
    """Inspect an authenticated repository's actual default branch HEAD."""
    if not _REPO.fullmatch(repository):
        raise ValueError("invalid repository name")
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    metadata = subprocess.run(
        ["gh", "api", f"repos/{repository}"],
        capture_output=True, text=True, timeout=timeout_seconds, check=False,
    )
    if metadata.returncode:
        raise RuntimeError(
            "authenticated repository read unavailable: " + metadata.stderr.strip()[:260]
        )
    payload = json.loads(metadata.stdout)
    if not isinstance(payload, dict) or str(payload.get("full_name","")).casefold() != repository.casefold():
        raise RuntimeError("GitHub repository identity mismatch")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,100}",branch):
        raise RuntimeError("repository default branch is unqualified")
    result = subprocess.run(
        ["gh","api",f"repos/{repository}/branches/{branch}"],
        text=True,capture_output=True,timeout=timeout_seconds,check=False,
    )
    if result.returncode:
        raise RuntimeError("default-branch read unavailable: " + result.stderr.strip()[:260])
    body = json.loads(result.stdout)
    sha = body.get("commit",{}).get("sha") if isinstance(body,dict) else None
    if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{40}",sha):
        raise RuntimeError("repository has no verifiable default-branch ref")
    return sha


def run_governed_portfolio_cycle(
    source_root: Path,
    runtime_root: Path,
    *,
    timeout_seconds: float = 120.0,
) -> dict[str, object]:
    """Run exactly one guarded PORTAL session generation.

    Requires an existing, explicitly qualified external execution route to
    dispatch work; otherwise the real runner fails closed. Never substitutes
    fake worker results or silently supplies effect authority.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    source_root = Path(source_root).resolve()
    runtime_root = Path(runtime_root).resolve()
    # An installed workstation cannot inherit capacity from a test fixture.
    nodes = runtime_root / "state" / "portal" / "qualified-nodes.yaml"
    projects = source_root / "registry" / "projects.yaml"
    if not nodes.is_file():
        raise RuntimeError(
            "No qualified live node manifest installed. "
            "PORTAL will not dispatch from tests/fixtures; configure "
            "state/portal/qualified-nodes.yaml after route qualification."
        )
    if not projects.is_file():
        raise RuntimeError("PORTAL curated project registry unavailable")
    db = runtime_root / "state" / "portal" / "desktop-portfolio.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-B", "-m", "portal.cli", "run",
        "--session-id", "portal-desktop",
        "--state-db", str(db),
        "--static-projects",
        "--projects", str(projects),
        "--nodes", str(nodes),
        "--host-bridge",
        "--once",
        "--max-parallel", "1",
        "--max-per-identity", "1",
        "--max-per-family", "1",
        "--max-per-lane", "1",
        "--discovered-effect-ceiling", "NO_EFFECT",
    ]
    result = subprocess.run(
        command, cwd=source_root, text=True, capture_output=True,
        timeout=timeout_seconds, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "PORTAL cycle not dispatched: "
            + (result.stderr.strip() or result.stdout.strip())[:500]
        )
    payload = json.loads(result.stdout)
    if payload.get("protected_effects_authorized") is not False:
        raise RuntimeError("PORTAL result missing protected-effect denial")
    return payload
