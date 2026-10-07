from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runner.portfolio_wave_scheduler import WaveAdmissionPlan


@dataclass(frozen=True)
class ExecutionNode:
    node_id: str
    max_parallel: int
    allowed_lanes: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        node_id = self.node_id.strip()
        if not node_id:
            raise ValueError("execution node id must not be empty")
        if type(self.max_parallel) is not int or self.max_parallel < 1:
            raise ValueError("execution node max_parallel must be a positive integer")
        if type(self.enabled) is not bool:
            raise ValueError("execution node enabled must be a boolean")

        lanes: set[str] = set()
        for raw_lane in self.allowed_lanes:
            if not isinstance(raw_lane, str):
                raise ValueError("execution node allowed lanes must be strings")
            lane = raw_lane.strip()
            if not lane:
                raise ValueError("execution node allowed lanes must not be empty")
            lanes.add(lane)

        object.__setattr__(self, "node_id", node_id)
        object.__setattr__(self, "allowed_lanes", tuple(sorted(lanes)))

    def allows_lane(self, lane_id: str) -> bool:
        return self.enabled and (
            not self.allowed_lanes or lane_id in self.allowed_lanes
        )


@dataclass(frozen=True)
class PortalAssignment:
    subject_kind: str
    subject_id: str
    lane_id: str
    node_id: str
    effect_ceiling: str
    action: str


@dataclass(frozen=True)
class PortalNodeDeferral:
    subject_kind: str
    subject_id: str
    lane_id: str
    reason: str


@dataclass(frozen=True)
class PortalPlan:
    runner_plan: "WaveAdmissionPlan"
    assignments: tuple[PortalAssignment, ...]
    node_deferrals: tuple[PortalNodeDeferral, ...]
    nodes: tuple[ExecutionNode, ...]

    def summary(self) -> dict[str, object]:
        by_node: dict[str, int] = {node.node_id: 0 for node in self.nodes}
        for assignment in self.assignments:
            by_node[assignment.node_id] += 1
        return {
            "runner": self.runner_plan.summary(),
            "assignments": len(self.assignments),
            "node_deferrals": len(self.node_deferrals),
            "assigned_by_node": dict(sorted(by_node.items())),
        }
