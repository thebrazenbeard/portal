from __future__ import annotations

from collections import Counter
from typing import Iterable

from runner.portfolio_advancement import AdvancementWave
from runner.portfolio_wave_scheduler import (
    WaveExecutionBudget,
    plan_wave_admission,
)

from .models import (
    ExecutionNode,
    PortalAssignment,
    PortalNodeDeferral,
    PortalPlan,
)


def plan_portal_wave(
    wave: AdvancementWave,
    *,
    budget: WaveExecutionBudget,
    nodes: Iterable[ExecutionNode],
    occupied_collision_keys: Iterable[str] = (),
) -> PortalPlan:
    """Assign Project Runner-admitted work to bounded execution nodes.

    Node placement is scheduling metadata only. This function does not grant
    repository/provider authority and performs no execution or protected effect.
    """
    declared_nodes = tuple(nodes)
    node_ids = [node.node_id for node in declared_nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("duplicate execution node id")

    ordered_nodes = tuple(sorted(declared_nodes, key=lambda node: node.node_id))
    runner_plan = plan_wave_admission(
        wave,
        budget=budget,
        occupied_collision_keys=occupied_collision_keys,
    )

    load: Counter[str] = Counter()
    assignments: list[PortalAssignment] = []
    node_deferrals: list[PortalNodeDeferral] = []

    for admission in runner_plan.selected:
        eligible = [
            node
            for node in ordered_nodes
            if node.allows_lane(admission.lane_id)
            and load[node.node_id] < node.max_parallel
        ]
        if not eligible:
            node_deferrals.append(
                PortalNodeDeferral(
                    subject_kind=admission.subject_kind,
                    subject_id=admission.subject_id,
                    lane_id=admission.lane_id,
                    reason="NO_EXECUTION_NODE",
                )
            )
            continue

        node = min(
            eligible,
            key=lambda candidate: (load[candidate.node_id], candidate.node_id),
        )
        assignments.append(
            PortalAssignment(
                subject_kind=admission.subject_kind,
                subject_id=admission.subject_id,
                lane_id=admission.lane_id,
                node_id=node.node_id,
                effect_ceiling=admission.effect_ceiling,
                action=admission.action,
            )
        )
        load[node.node_id] += 1

    return PortalPlan(
        runner_plan=runner_plan,
        assignments=tuple(assignments),
        node_deferrals=tuple(node_deferrals),
        nodes=ordered_nodes,
    )
