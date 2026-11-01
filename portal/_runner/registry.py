from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable, TypeVar

import yaml

from .models import DependencyEdge, Observation, ProjectDefinition, WorkerDefinition
from .schema import validate_document


T = TypeVar("T")


@dataclass(frozen=True)
class ProjectRegistrySnapshot:
    projects: tuple[ProjectDefinition, ...]
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class WorkerRegistrySnapshot:
    workers: tuple[WorkerDefinition, ...]
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class DependencyRegistrySnapshot:
    dependencies: tuple[DependencyEdge, ...]
    sha256: str
    byte_length: int


def _load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _reject_duplicate_ids(records: Iterable[T], kind: str) -> tuple[T, ...]:
    seen: set[str] = set()
    result: list[T] = []
    for record in records:
        record_id = getattr(record, "id")
        if record_id in seen:
            raise ValueError(f"duplicate {kind} id: {record_id}")
        seen.add(record_id)
        result.append(record)
    return tuple(result)


def load_worker_snapshot(path: Path) -> WorkerRegistrySnapshot:
    raw = path.read_bytes()
    text = raw.decode("utf-8", "strict")
    payload = yaml.safe_load(text)
    validate_document("worker", payload)
    assert isinstance(payload, dict)
    workers = (WorkerDefinition.from_mapping(item) for item in payload["workers"])
    return WorkerRegistrySnapshot(
        workers=_reject_duplicate_ids(workers, "worker"),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )


def load_workers(path: Path) -> tuple[WorkerDefinition, ...]:
    return load_worker_snapshot(path).workers


def _project_snapshot_from_bytes(
    raw: bytes,
    *,
    require_scope_metadata: bool,
) -> ProjectRegistrySnapshot:
    text = raw.decode("utf-8", "strict")
    payload = yaml.safe_load(text)
    validate_document("project", payload)
    assert isinstance(payload, dict)

    if require_scope_metadata:
        required_scope_fields = {
            "assignment_scope",
            "review_scope",
            "family_id",
            "scheduling_state",
        }
        if any(
            not required_scope_fields.issubset(item)
            for item in payload["projects"]
        ):
            raise ValueError(
                "external project registry requires explicit "
                "assignment_scope, review_scope, family_id, and scheduling_state"
            )

    projects = (
        ProjectDefinition.from_mapping(item)
        for item in payload["projects"]
    )
    return ProjectRegistrySnapshot(
        projects=_reject_duplicate_ids(projects, "project"),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )


def load_project_snapshot(
    path: Path,
    *,
    require_scope_metadata: bool = False,
) -> ProjectRegistrySnapshot:
    try:
        raw = path.read_bytes()
        return _project_snapshot_from_bytes(
            raw,
            require_scope_metadata=require_scope_metadata,
        )
    except Exception as exc:
        if require_scope_metadata:
            safe_scope_error = (
                "external project registry requires explicit "
                "assignment_scope, review_scope, family_id, and scheduling_state"
            )
            if isinstance(exc, ValueError) and str(exc) == safe_scope_error:
                raise ValueError(safe_scope_error) from None
            raise ValueError(
                "external project registry is unavailable or structurally invalid"
            ) from None
        raise


def load_projects(
    path: Path,
    *,
    require_scope_metadata: bool = False,
) -> tuple[ProjectDefinition, ...]:
    return load_project_snapshot(
        path,
        require_scope_metadata=require_scope_metadata,
    ).projects


def _dependency_snapshot_from_bytes(
    raw: bytes,
) -> DependencyRegistrySnapshot:
    text = raw.decode("utf-8", "strict")
    payload = yaml.safe_load(text)
    validate_document("dependency", payload)
    assert isinstance(payload, dict)
    edges = (
        DependencyEdge.from_mapping(item)
        for item in payload["dependencies"]
    )
    return DependencyRegistrySnapshot(
        dependencies=_reject_duplicate_ids(edges, "dependency"),
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )


def load_dependency_snapshot(path: Path) -> DependencyRegistrySnapshot:
    raw = path.read_bytes()
    return _dependency_snapshot_from_bytes(raw)


def load_dependencies(path: Path) -> tuple[DependencyEdge, ...]:
    return load_dependency_snapshot(path).dependencies


def load_observations(path: Path) -> tuple[Observation, ...]:
    payload = _load_yaml(path)
    validate_document("observation", payload)
    assert isinstance(payload, dict)
    return tuple(
        Observation.from_mapping(item)
        for item in payload["observations"]
    )
