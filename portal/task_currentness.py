from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from runner.task_monitor import (
    reconcile_orphaned_tasks,
    summarize_tasks,
)


@dataclass(frozen=True)
class PortalTaskCurrentnessSnapshot:
    node_id: str
    tasks_root: Path
    occupied_slots: int
    reconciled_unknown_exit: int
    state_counts: dict[str, int]


class LocalProjectRunnerTaskCurrentness:
    """Derive fail-closed local node occupancy from Project Runner task state.

    Reconciliation never kills or replays a process. Project Runner first
    archives task registrations whose process is gone or whose PID identity was
    reused as UNKNOWN_EXIT. Every record still present in the active monitor
    then consumes capacity until stronger evidence frees it.
    """

    def __init__(
        self,
        *,
        node_id: str,
        tasks_root: Path,
    ) -> None:
        node_id = node_id.strip()
        if not node_id:
            raise ValueError("node_id is required")
        self.node_id = node_id
        self.tasks_root = Path(tasks_root)

    def snapshot(self) -> PortalTaskCurrentnessSnapshot:
        reconciled = reconcile_orphaned_tasks(self.tasks_root)
        summary = summarize_tasks(self.tasks_root)
        raw_tasks = summary.get("tasks")
        if not isinstance(raw_tasks, list):
            raise ValueError("Project Runner task summary must contain a task list")

        state_counts: dict[str, int] = {}
        for raw_task in raw_tasks:
            if isinstance(raw_task, Mapping):
                raw_state = raw_task.get("state")
                state = (
                    str(raw_state).strip()
                    if raw_state is not None and str(raw_state).strip()
                    else "IDENTITY_UNVERIFIED"
                )
            else:
                state = "IDENTITY_UNVERIFIED"
            state_counts[state] = state_counts.get(state, 0) + 1

        return PortalTaskCurrentnessSnapshot(
            node_id=self.node_id,
            tasks_root=self.tasks_root,
            occupied_slots=len(raw_tasks),
            reconciled_unknown_exit=len(reconciled),
            state_counts=dict(sorted(state_counts.items())),
        )

    def __call__(self) -> dict[str, int]:
        snapshot = self.snapshot()
        return {snapshot.node_id: snapshot.occupied_slots}
