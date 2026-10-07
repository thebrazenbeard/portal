from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Iterable, Protocol
from urllib import error, parse, request

import yaml

from runner.models import (
    ProjectAssignmentScope,
    ProjectDefinition,
    ProjectReviewScope,
    ProjectSchedulingState,
)
from runner.registry import ProjectRegistrySnapshot


@dataclass(frozen=True)
class RepositoryInventoryItem:
    name: str
    full_name: str
    private: bool
    archived: bool
    default_branch: str

    def __post_init__(self) -> None:
        name = self.name.strip()
        full_name = self.full_name.strip()
        default_branch = self.default_branch.strip()
        if not name:
            raise ValueError("discovered repository name is required")
        if "/" not in full_name or any(ch.isspace() for ch in full_name):
            raise ValueError("discovered repository full_name must be owner/name")
        if not default_branch:
            raise ValueError("discovered repository default_branch is required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "full_name", full_name)
        object.__setattr__(self, "default_branch", default_branch)


class RepositoryCatalog(Protocol):
    def list_owned_repositories(
        self,
        owner: str,
    ) -> tuple[RepositoryInventoryItem, ...]:
        ...


class GitHubRepositoryCatalog:
    """Read-only GitHub repository inventory adapter.

    With a token this uses the authenticated user's repository listing so
    private owned repositories can be observed. Without a token it falls back
    to the public owner listing. It never writes credentials or inventory.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout: float = 20.0,
    ) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def _get_json(self, url: str) -> object:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "portal-portfolio-discovery/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            raise RuntimeError("GitHub repository discovery failed") from exc
        try:
            return json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "GitHub repository discovery returned invalid JSON"
            ) from exc

    def list_owned_repositories(
        self,
        owner: str,
    ) -> tuple[RepositoryInventoryItem, ...]:
        owner = owner.strip()
        if not owner:
            raise ValueError("portfolio owner is required")

        results: list[RepositoryInventoryItem] = []
        page = 1
        while True:
            if self.token:
                query = parse.urlencode(
                    {
                        "affiliation": "owner",
                        "per_page": 100,
                        "page": page,
                        "sort": "full_name",
                        "direction": "asc",
                    }
                )
                url = f"{self.api_base}/user/repos?{query}"
            else:
                query = parse.urlencode(
                    {
                        "type": "owner",
                        "per_page": 100,
                        "page": page,
                        "sort": "full_name",
                        "direction": "asc",
                    }
                )
                owner_path = parse.quote(owner, safe="")
                url = f"{self.api_base}/users/{owner_path}/repos?{query}"

            payload = self._get_json(url)
            if not isinstance(payload, list):
                raise RuntimeError(
                    "GitHub repository discovery returned non-list payload"
                )
            for raw in payload:
                if not isinstance(raw, dict):
                    raise RuntimeError(
                        "GitHub repository discovery returned malformed item"
                    )
                owner_payload = raw.get("owner")
                observed_owner = (
                    str(owner_payload.get("login", ""))
                    if isinstance(owner_payload, dict)
                    else ""
                )
                if observed_owner and observed_owner.casefold() != owner.casefold():
                    continue
                results.append(
                    RepositoryInventoryItem(
                        name=str(raw.get("name", "")),
                        full_name=str(raw.get("full_name", "")),
                        private=bool(raw.get("private", False)),
                        archived=bool(raw.get("archived", False)),
                        default_branch=str(raw.get("default_branch", "")),
                    )
                )

            if len(payload) < 100:
                break
            page += 1

        return tuple(
            sorted(results, key=lambda item: item.full_name.casefold())
        )


def _project_mapping(project: ProjectDefinition) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": project.id,
        "name": project.name,
        "visibility": project.visibility,
        "repositories": list(project.repositories),
        "capabilities": list(project.capabilities),
        "assignment_scope": project.assignment_scope.value,
        "review_scope": project.review_scope.value,
        "family_id": project.family_id,
        "scheduling_state": project.scheduling_state.value,
    }
    if project.execution_targets:
        payload["execution_targets"] = [
            {
                "work_type": target.work_type,
                "repository": target.repository,
                "ref": target.ref,
                **(
                    {
                        "worker_id": target.worker_id,
                        "worker_route": target.worker_route.value,
                    }
                    if target.worker_id is not None
                    and target.worker_route is not None
                    else {}
                ),
            }
            for target in project.execution_targets
        ]
    if project.scope_note is not None:
        payload["scope_note"] = project.scope_note
    return payload


def _registry_bytes(
    projects: tuple[ProjectDefinition, ...],
) -> bytes:
    payload = {
        "projects": [
            _project_mapping(project)
            for project in sorted(projects, key=lambda item: item.id.casefold())
        ]
    }
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return text.encode("utf-8")


def build_live_project_registry(
    *,
    owner: str,
    repositories: Iterable[RepositoryInventoryItem],
    curated_projects: Iterable[ProjectDefinition] = (),
) -> ProjectRegistrySnapshot:
    """Build an exact live membership overlay without inventing work authority."""

    owner = owner.strip()
    if not owner:
        raise ValueError("portfolio owner is required")

    discovered = tuple(repositories)
    by_repo: dict[str, RepositoryInventoryItem] = {}
    for repo in discovered:
        prefix, _, _name = repo.full_name.partition("/")
        if prefix.casefold() != owner.casefold():
            raise ValueError(
                "discovered repository is outside requested portfolio owner"
            )
        key = repo.full_name.casefold()
        if key in by_repo:
            raise ValueError(
                f"duplicate discovered repository: {repo.full_name}"
            )
        by_repo[key] = repo

    curated_by_repo: dict[str, list[ProjectDefinition]] = {}
    for project in curated_projects:
        for repository in project.repositories:
            curated_by_repo.setdefault(
                repository.casefold(),
                [],
            ).append(project)

    projects: list[ProjectDefinition] = []
    seen_ids: set[str] = set()

    for key, repo in sorted(
        by_repo.items(),
        key=lambda item: item[1].full_name.casefold(),
    ):
        matches = curated_by_repo.get(key, [])
        if len(matches) > 1:
            raise ValueError(
                f"curated repository binding is ambiguous: {repo.full_name}"
            )

        visibility = "private" if repo.private else "public"
        live_state = (
            ProjectSchedulingState.ARCHIVED
            if repo.archived
            else ProjectSchedulingState.SCHEDULABLE
        )

        if matches:
            curated = matches[0]
            if len(curated.repositories) != 1:
                raise ValueError(
                    "curated multi-repository project requires explicit merge policy"
                )
            scheduling_state = (
                ProjectSchedulingState.ARCHIVED
                if repo.archived
                else curated.scheduling_state
            )
            project = replace(
                curated,
                visibility=visibility,
                scheduling_state=scheduling_state,
            )
        else:
            project_id = repo.name.casefold()
            project = ProjectDefinition(
                id=project_id,
                name=repo.name,
                visibility=visibility,
                repositories=(repo.full_name,),
                capabilities=("read", "analyze", "propose"),
                assignment_scope=ProjectAssignmentScope.NONE,
                review_scope=ProjectReviewScope.NONE,
                scheduling_state=live_state,
                family_id=project_id,
                execution_targets=(),
                scope_note=(
                    "Live-discovered repository; no execution authority "
                    "or target inferred from membership."
                ),
            )

        project_key = project.id.casefold()
        if project_key in seen_ids:
            raise ValueError(
                f"duplicate live project id: {project.id}"
            )
        seen_ids.add(project_key)
        projects.append(project)

    project_tuple = tuple(
        sorted(projects, key=lambda item: item.id.casefold())
    )
    raw = _registry_bytes(project_tuple)
    return ProjectRegistrySnapshot(
        projects=project_tuple,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )


def discover_live_project_registry(
    *,
    owner: str,
    curated_projects: Iterable[ProjectDefinition] = (),
    token: str | None = None,
    catalog: RepositoryCatalog | None = None,
) -> ProjectRegistrySnapshot:
    source = catalog or GitHubRepositoryCatalog(token=token)
    repositories = source.list_owned_repositories(owner)
    return build_live_project_registry(
        owner=owner,
        repositories=repositories,
        curated_projects=curated_projects,
    )


def write_project_registry(
    path: Path,
    snapshot: ProjectRegistrySnapshot,
) -> None:
    """Write the private/local live registry selected by the operator."""

    raw = _registry_bytes(snapshot.projects)
    observed = hashlib.sha256(raw).hexdigest()
    if observed != snapshot.sha256 or len(raw) != snapshot.byte_length:
        raise ValueError(
            "live registry snapshot digest diverges from rendered content"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
