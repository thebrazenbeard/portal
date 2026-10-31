from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .work_units import WorkUnit, work_unit_fingerprint


@dataclass(frozen=True)
class BackendResult:
    work_fingerprint: str
    succeeded: bool
    outputs: tuple[str, ...]
    evidence: tuple[str, ...]
    classification: str = "SUCCEEDED"


class ExecutionBackend(Protocol):
    def execute(self, work: WorkUnit) -> BackendResult: ...


class MockBackend:
    """Deterministic side-effect-free backend for M4/M5 tests."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.executed: list[str] = []

    def execute(self, work: WorkUnit) -> BackendResult:
        fingerprint = work_unit_fingerprint(work)
        self.executed.append(fingerprint)
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=self.succeed,
            outputs=tuple(work.expected_outputs) if self.succeed else (),
            evidence=("mock-backend",),
            classification="SUCCEEDED" if self.succeed else "FAILED",
        )
