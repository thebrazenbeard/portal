"""Regression: a thirteen-repository wave must never invent host capacity."""
from portal.coordinator import plan_portal_wave
from portal.models import ExecutionNode
from runner.portfolio_advancement import AdvancementItem, AdvancementWave
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _wave_of_13():
    items = tuple(
        AdvancementItem(
            subject_kind="repository",
            subject_id=f"p0-{i:02d}",
            repositories=(f"owner/repo-{i:02d}",),
            priority="P0",
            family_id=f"family-{i:02d}",
            activity_state="ACTIVE",
            lead_identity="ONE",
            reviewer_identities=("REZON",),
            action="EXECUTE_FRONTIER",
            execution_state="QUEUED",
            effect_ceiling="SOURCE_ONLY",
            review_gate="EXACT_HEAD_REVIEW",
            frontier="source-only repair",
            source_status="UNVERIFIED",
            lane_id=f"lane-{i:02d}",
        )
        for i in range(13)
    )
    return AdvancementWave(
        wave_id="test-thirteen-source-lanes",
        generated_at="2026-10-09",
        corpus_binding={},
        identities={},
        policy={},
        items=items,
    )


def _budget(global_slots):
    return WaveExecutionBudget(
        max_parallel=global_slots,
        max_per_identity=global_slots,
        max_per_family=global_slots,
        max_per_lane=global_slots,
    )


def test_thirteen_ready_lanes_cannot_oversubscribe_single_resident_node():
    plan = plan_portal_wave(
        _wave_of_13(),
        budget=_budget(13),
        nodes=(ExecutionNode(node_id="desktop-local", max_parallel=1),),
    )
    assert len(plan.runner_plan.selected) == 13
    assert len(plan.assignments) == 1
    assert len(plan.node_deferrals) == 12
    assert {x.reason for x in plan.node_deferrals} == {"NO_EXECUTION_NODE"}
    assert plan.assignments[0].node_id == "desktop-local"


def test_global_one_slot_budget_defers_other_twelve_before_node_placement():
    plan = plan_portal_wave(
        _wave_of_13(),
        budget=_budget(1),
        nodes=(ExecutionNode(node_id="desktop-local", max_parallel=1),),
    )
    assert len(plan.assignments) == 1
    assert len(plan.runner_plan.deferred) == 12
    assert {x.reason for x in plan.runner_plan.deferred} == {"GLOBAL_BUDGET"}
    assert not plan.node_deferrals
