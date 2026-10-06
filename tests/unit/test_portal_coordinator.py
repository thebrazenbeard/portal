from __future__ import annotations

import pytest

from portal.coordinator import plan_portal_wave
from portal.models import ExecutionNode
from runner.portfolio_advancement import AdvancementItem, AdvancementWave
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _item(
    subject_id: str,
    repository: str,
    *,
    lane: str = "vera",
    lead: str = "vera",
) -> AdvancementItem:
    return AdvancementItem(
        subject_kind="repository",
        subject_id=subject_id,
        repositories=(repository,),
        priority="P1",
        family_id=subject_id,
        activity_state="ACTIVE",
        lead_identity=lead,
        reviewer_identities=(),
        action="ADVANCE",
        execution_state="QUEUED",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"advance {subject_id}",
        source_status="CURRENT",
        lane_id=lane,
    )


def _wave(*items: AdvancementItem) -> AdvancementWave:
    return AdvancementWave(
        wave_id="portal-test-wave",
        generated_at="2026-10-05T00:00:00Z",
        corpus_binding={},
        identities={},
        policy={},
        items=tuple(items),
    )


def _budget(max_parallel: int = 8) -> WaveExecutionBudget:
    return WaveExecutionBudget(
        max_parallel=max_parallel,
        max_per_identity=max_parallel,
        max_per_family=max_parallel,
        max_per_lane=max_parallel,
    )


def test_balances_deterministically_across_nodes() -> None:
    wave = _wave(
        _item("a", "example/a"),
        _item("b", "example/b"),
        _item("c", "example/c"),
        _item("d", "example/d"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(4),
        nodes=(
            ExecutionNode(node_id="zeta", max_parallel=2),
            ExecutionNode(node_id="alpha", max_parallel=2),
        ),
    )

    assert [(item.subject_id, item.node_id) for item in plan.assignments] == [
        ("a", "alpha"),
        ("b", "zeta"),
        ("c", "alpha"),
        ("d", "zeta"),
    ]
    assert plan.node_deferrals == ()


def test_respects_lane_allowlists() -> None:
    wave = _wave(
        _item("a", "example/a", lane="lane-a"),
        _item("b", "example/b", lane="lane-b"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(2),
        nodes=(
            ExecutionNode(
                node_id="lappy",
                max_parallel=1,
                allowed_lanes=("lane-a",),
            ),
            ExecutionNode(
                node_id="worklaptop",
                max_parallel=1,
                allowed_lanes=("lane-b",),
            ),
        ),
    )

    assert [(item.subject_id, item.node_id) for item in plan.assignments] == [
        ("a", "lappy"),
        ("b", "worklaptop"),
    ]


def test_disabled_and_exhausted_nodes_do_not_receive_work() -> None:
    wave = _wave(
        _item("a", "example/a"),
        _item("b", "example/b"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(2),
        nodes=(
            ExecutionNode(node_id="disabled", max_parallel=9, enabled=False),
            ExecutionNode(node_id="only", max_parallel=1),
        ),
    )

    assert [(item.subject_id, item.node_id) for item in plan.assignments] == [
        ("a", "only"),
    ]
    assert [(item.subject_id, item.reason) for item in plan.node_deferrals] == [
        ("b", "NO_EXECUTION_NODE"),
    ]


def test_duplicate_node_ids_fail_closed() -> None:
    with pytest.raises(ValueError, match="duplicate execution node id"):
        plan_portal_wave(
            _wave(_item("a", "example/a")),
            budget=_budget(1),
            nodes=(
                ExecutionNode(node_id="same", max_parallel=1),
                ExecutionNode(node_id="same", max_parallel=1),
            ),
        )


def test_project_runner_collision_decision_is_preserved() -> None:
    wave = _wave(
        _item("a", "example/shared"),
        _item("b", "example/shared"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(2),
        nodes=(ExecutionNode(node_id="lappy", max_parallel=2),),
    )

    assert [item.subject_id for item in plan.assignments] == ["a"]
    assert [(item.subject_id, item.reason) for item in plan.runner_plan.deferred] == [
        ("b", "COLLISION"),
    ]



def test_active_subjects_consume_budget_before_new_assignments() -> None:
    wave = _wave(
        _item("a", "example/a"),
        _item("b", "example/b"),
        _item("c", "example/c"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(2),
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=2),),
        active_subjects=(("repository", "a"),),
    )

    assert [item.subject_id for item in plan.assignments] == ["b"]
    assert ("a", "ALREADY_ACTIVE") in [
        (item.subject_id, item.reason)
        for item in plan.runner_plan.deferred
    ]

def test_live_occupied_slots_keep_new_work_off_saturated_node() -> None:
    wave = _wave(
        _item("a", "example/a"),
        _item("b", "example/b"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(2),
        nodes=(
            ExecutionNode(node_id="lappy", max_parallel=8),
            ExecutionNode(node_id="worklaptop", max_parallel=8),
        ),
        occupied_node_slots={
            "lappy": 54,
            "worklaptop": 0,
        },
    )

    assert [(item.subject_id, item.node_id) for item in plan.assignments] == [
        ("a", "worklaptop"),
        ("b", "worklaptop"),
    ]


def test_unknown_live_occupancy_node_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown execution node"):
        plan_portal_wave(
            _wave(_item("a", "example/a")),
            budget=_budget(1),
            nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
            occupied_node_slots={"ghost": 1},
        )


def test_blocked_subject_reserves_collision_without_using_assignment_budget() -> None:
    wave = _wave(
        _item("blocked", "example/shared", lead="vera"),
        _item("colliding", "example/shared", lead="review"),
        _item("unrelated", "example/unrelated", lead="vera"),
    )
    plan = plan_portal_wave(
        wave,
        budget=_budget(1),
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        blocked_subjects=(("repository", "blocked"),),
    )

    assert [item.subject_id for item in plan.assignments] == ["unrelated"]
    reasons = {
        item.subject_id: item.reason
        for item in plan.runner_plan.deferred
    }
    assert reasons["blocked"] == "BLOCKED_ACTIVE"
    assert reasons["colliding"] == "COLLISION"
