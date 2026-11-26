from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Mapping, Any

from .models import ExactSubject


class WorkUnitStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_DETERMINISTIC = "FAILED_DETERMINISTIC"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class WorkUnit:
    id: str
    root_frontier_id: str
    parent_work_id: str | None
    inputs: tuple[ExactSubject, ...]
    operation: str
    required_capabilities: tuple[str, ...]
    collision_keys: tuple[str, ...]
    recursion_depth: int
    budget_allocation: Mapping[str, int]
    expected_outputs: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    status: WorkUnitStatus
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("work id is required")
        if not self.root_frontier_id.strip():
            raise ValueError("root frontier id is required")
        if not self.operation.strip():
            raise ValueError("operation is required")
        if self.recursion_depth < 0:
            raise ValueError("recursion depth must be non-negative")
        if any(int(value) < 0 for value in self.budget_allocation.values()):
            raise ValueError("budget allocation must be non-negative")
        try:
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("work payload must be JSON-serializable") from exc


def _subject_payload(subject: ExactSubject) -> dict[str, str | None]:
    return {
        "repository": subject.repository,
        "ref": subject.ref,
        "commit": subject.commit,
        "path": subject.path,
        "digest": subject.digest,
    }


def work_unit_fingerprint(work: WorkUnit) -> str:
    payload = {
        "inputs": sorted(
            (_subject_payload(subject) for subject in work.inputs),
            key=lambda item: (
                item["repository"] or "",
                item["ref"] or "",
                item["commit"] or "",
                item["path"] or "",
                item["digest"] or "",
            ),
        ),
        "operation": work.operation,
        "required_capabilities": sorted(set(work.required_capabilities)),
        "collision_keys": sorted(set(work.collision_keys)),
        "expected_outputs": sorted(set(work.expected_outputs)),
        "completion_criteria": sorted(set(work.completion_criteria)),
        "payload": work.payload,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
