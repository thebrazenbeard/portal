import json
from pathlib import Path

import pytest

from runner.portfolio_advancement import (
    AdvancementItem,
    AdvancementWave,
    load_advancement_wave,
)
from runner.portfolio_wave_scheduler import (
    WaveExecutionBudget,
    collision_keys,
    plan_wave_admission,
)


ROOT = Path(__file__).resolve().parents[2]


def item(
    subject_id,
    *,
    repository="owner/repo",
    priority="P1",
    lead="ONE",
    action="EXECUTE_FRONTIER",
    state="QUEUED",
    ceiling="SOURCE_ONLY",
    kind="repository",
    family="test-family",
):
    kwargs = {
        "subject_kind": kind,
        "subject_id": subject_id,
        "repositories": (repository,),
        "priority": priority,
        "family_id": family,
        "activity_state": "ACTIVE",
        "lead_identity": lead,
        "reviewer_identities": ("REZON",),
        "action": action,
        "execution_state": state,
        "effect_ceiling": ceiling,
        "review_gate": "EXACT_HEAD_REVIEW",
        "frontier": "test frontier",
        "source_status": "test status",
    }
    return AdvancementItem(**kwargs)


def wave(*items):
    return AdvancementWave(
        wave_id="PROJECT_RUNNER_PORTFOLIO_ADVANCEMENT_WAVE_V1",
        generated_at="2026-09-24T00:00:00Z",
        corpus_binding={},
        identities={
            "ONE": "orchestrator",
            "REZON": "assurance",
            "VOSS": "forensic",
        },
        policy={},
        items=tuple(items),
    )


def test_budget_must_be_explicit_and_valid():
    with pytest.raises(ValueError, match="positive integer"):
        plan_wave_admission(
            wave(item("a")),
            budget=WaveExecutionBudget(max_parallel=0, max_per_identity=1, max_per_family=1),
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        plan_wave_admission(
            wave(item("a")),
            budget=WaveExecutionBudget(max_parallel=1, max_per_identity=2, max_per_family=1),
        )


def test_priority_and_identity_budget_bound_selection():
    planned = plan_wave_admission(
        wave(
            item("p1-a", priority="P1", lead="ONE", repository="owner/a"),
            item("p0-b", priority="P0", lead="ONE", repository="owner/b"),
            item("p0-c", priority="P0", lead="VOSS", repository="owner/c"),
            item("p2-d", priority="P2", lead="REZON", repository="owner/d"),
        ),
        budget=WaveExecutionBudget(max_parallel=3, max_per_identity=1, max_per_family=3),
    )
    assert [selected.subject_id for selected in planned.selected] == [
        "p0-b",
        "p0-c",
        "p2-d",
    ]
    assert any(
        deferred.subject_id == "p1-a"
        and deferred.reason == "IDENTITY_BUDGET"
        for deferred in planned.deferred
    )


def test_family_budget_blocks_cross_identity_same_family():
    planned = plan_wave_admission(
        wave(
            item(
                "alpha",
                repository="owner/alpha",
                priority="P0",
                lead="ONE",
            ),
            item(
                "beta",
                repository="owner/beta",
                priority="P0",
                lead="VOSS",
            ),
        ),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=1,
            max_per_family=1,
        ),
    )
    assert [selected.subject_id for selected in planned.selected] == ["alpha"]
    blocked = next(
        deferred for deferred in planned.deferred
        if deferred.subject_id == "beta"
    )
    assert blocked.reason == "FAMILY_BUDGET"


def test_repository_collision_blocks_overlapping_workstream():
    repository = item(
        "repo",
        priority="P0",
        lead="ONE",
        repository="owner/shared",
    )
    workstream = item(
        "workstream",
        priority="P1",
        lead="VOSS",
        repository="owner/shared",
        kind="workstream",
    )
    planned = plan_wave_admission(
        wave(repository, workstream),
        budget=WaveExecutionBudget(max_parallel=2, max_per_identity=1, max_per_family=2),
    )
    assert [selected.subject_id for selected in planned.selected] == ["repo"]
    blocked = next(
        deferred for deferred in planned.deferred
        if deferred.subject_id == "workstream"
    )
    assert blocked.reason == "COLLISION"
    assert blocked.collision_keys == ("repository:owner/shared",)


def test_existing_occupied_collision_key_blocks_admission():
    planned = plan_wave_admission(
        wave(item("repo", repository="owner/shared")),
        budget=WaveExecutionBudget(max_parallel=1, max_per_identity=1, max_per_family=1),
        occupied_collision_keys=("repository:owner/shared",),
    )
    assert planned.selected == ()
    assert planned.deferred[0].reason == "COLLISION"


def test_held_item_never_executes():
    held = item(
        "old",
        state="HELD",
        action="PRESERVE_ONLY",
        ceiling="NO_EFFECT",
    )
    planned = plan_wave_admission(
        wave(held),
        budget=WaveExecutionBudget(max_parallel=1, max_per_identity=1, max_per_family=1),
    )
    assert planned.selected == ()
    assert planned.deferred[0].reason == "NOT_QUEUED"


def test_queued_no_effect_research_is_schedulable_without_source_authority():
    research = item(
        "research",
        action="CURRENTNESS_AUDIT",
        ceiling="NO_EFFECT",
    )
    planned = plan_wave_admission(
        wave(research),
        budget=WaveExecutionBudget(max_parallel=1, max_per_identity=1, max_per_family=1),
    )
    assert [selected.subject_id for selected in planned.selected] == ["research"]
    assert planned.selected[0].effect_ceiling == "NO_EFFECT"
    assert planned.deferred == ()


def test_real_public_wave_produces_bounded_collision_free_slice():
    public_wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    planned = plan_wave_admission(
        public_wave,
        budget=WaveExecutionBudget(max_parallel=6, max_per_identity=1, max_per_family=1),
    )
    assert len(planned.selected) <= 6
    assert len({item.lead_identity for item in planned.selected}) == len(
        planned.selected
    )
    all_keys = [
        key
        for selected in planned.selected
        for key in selected.collision_keys
    ]
    assert len(all_keys) == len(set(all_keys))
    assert all(selected.priority in {"P0", "P1", "P2", "P3", "P4"} for selected in planned.selected)


def test_non_repository_surface_gets_explicit_collision_key():
    workstream = item(
        "workspace",
        repository="Google Drive staging",
        kind="workstream",
    )
    assert collision_keys(workstream) == ("surface:google drive staging",)


def test_advancement_item_can_declare_explicit_lane():
    mapped = AdvancementItem.from_mapping({
        "subject_kind": "repository",
        "subject_id": "research-a",
        "repository": "owner/research-a",
        "priority": "P1",
        "family_id": "research",
        "activity_state": "ACTIVE",
        "lead_identity": "ONE",
        "reviewer_identities": ["REZON"],
        "action": "CURRENTNESS_AUDIT",
        "execution_state": "QUEUED",
        "effect_ceiling": "NO_EFFECT",
        "review_gate": "EXACT_HEAD_REVIEW",
        "frontier": "inspect",
        "source_status": "queued",
        "lane": "research",
    })
    assert mapped.lane_id == "research"


def test_lane_budget_allows_independent_progress_without_bypassing_collisions():
    planned = plan_wave_admission(
        wave(
            item("a1", priority="P0", lead="ONE", repository="owner/a1"),
            item("a2", priority="P0", lead="ONE", repository="owner/a2"),
            item("b1", priority="P0", lead="VOSS", repository="owner/b1"),
        ),
        budget=WaveExecutionBudget(
            max_parallel=3,
            max_per_identity=3,
            max_per_family=3,
            max_per_lane=1,
        ),
    )
    assert [selected.subject_id for selected in planned.selected] == ["a1", "b1"]
    assert any(
        deferred.subject_id == "a2" and deferred.reason == "LANE_BUDGET"
        for deferred in planned.deferred
    )


def test_wave_schema_accepts_explicit_lane_without_requiring_it_everywhere(
    tmp_path,
):
    source = json.loads(
        (ROOT / "portfolio" / "advancement_wave.public.json").read_text(
            encoding="utf-8"
        )
    )
    source["items"][0]["lane"] = "integration"
    path = tmp_path / "wave.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    loaded = load_advancement_wave(path)
    assert loaded.items[0].lane_id == "integration"
    assert any(item.lane_id is None for item in loaded.items[1:])


def test_lane_is_bound_in_plan_summary_but_not_a_collision_override():
    shared_a = item("shared-a", repository="owner/shared", lead="ONE")
    shared_b = item("shared-b", repository="owner/shared", lead="VOSS")
    planned = plan_wave_admission(
        wave(shared_a, shared_b),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=2,
            max_per_family=2,
            max_per_lane=2,
        ),
    )
    assert [item.subject_id for item in planned.selected] == ["shared-a"]
    assert planned.deferred[0].reason == "COLLISION"
    assert planned.summary()["selected_by_lane"] == {"ONE": 1}



def test_terminal_subject_exclusion_refills_with_next_eligible_subject():
    planned = plan_wave_admission(
        wave(
            item("a", priority="P0", repository="owner/a"),
            item("b", priority="P0", repository="owner/b"),
            item("c", priority="P1", repository="owner/c"),
        ),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
        ),
        excluded_subjects=(("repository", "a"),),
    )

    assert [selected.subject_id for selected in planned.selected] == ["b"]
    excluded = next(
        value for value in planned.deferred if value.subject_id == "a"
    )
    assert excluded.reason == "EXCLUDED_TERMINAL"
    assert planned.excluded_subjects == (("repository", "a"),)
    assert planned.summary()["excluded_subjects"] == ["repository:a"]


def test_excluded_subject_identity_is_kind_scoped():
    planned = plan_wave_admission(
        wave(
            item("same", repository="owner/repo", kind="repository"),
            item(
                "same",
                repository="surface-a",
                kind="workstream",
                lead="VOSS",
                family="other",
            ),
        ),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=1,
            max_per_family=1,
        ),
        excluded_subjects=(("repository", "same"),),
    )

    assert [selected.subject_kind for selected in planned.selected] == [
        "workstream"
    ]



def test_active_subjects_seed_global_identity_family_lane_and_collision_loads():
    active = item(
        "active",
        priority="P0",
        lead="ONE",
        repository="owner/active",
        family="family-a",
    )
    same_identity = item(
        "same-identity",
        priority="P0",
        lead="ONE",
        repository="owner/other",
        family="family-b",
    )
    other_identity = item(
        "other-identity",
        priority="P0",
        lead="VOSS",
        repository="owner/voss",
        family="family-c",
    )
    colliding_workstream = item(
        "colliding-workstream",
        priority="P0",
        lead="REZON",
        repository="owner/active",
        kind="workstream",
        family="family-d",
    )

    planned = plan_wave_admission(
        wave(active, same_identity, other_identity, colliding_workstream),
        budget=WaveExecutionBudget(
            max_parallel=2,
            max_per_identity=1,
            max_per_family=2,
            max_per_lane=1,
        ),
        active_subjects=(("repository", "active"),),
    )

    assert [selected.subject_id for selected in planned.selected] == [
        "other-identity"
    ]
    reasons = {
        deferred.subject_id: deferred.reason
        for deferred in planned.deferred
    }
    assert reasons["active"] == "ALREADY_ACTIVE"
    assert reasons["same-identity"] == "LANE_BUDGET"
    assert reasons["colliding-workstream"] == "COLLISION"
    assert planned.active_subjects == (("repository", "active"),)
    assert planned.summary()["active_subjects"] == ["repository:active"]


def test_active_subjects_consume_global_parallel_budget():
    planned = plan_wave_admission(
        wave(
            item("active", repository="owner/active", family="a"),
            item(
                "next",
                repository="owner/next",
                lead="VOSS",
                family="b",
            ),
        ),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
        ),
        active_subjects=(("repository", "active"),),
    )

    assert planned.selected == ()
    deferred = next(
        item for item in planned.deferred if item.subject_id == "next"
    )
    assert deferred.reason == "GLOBAL_BUDGET"


def test_active_subject_must_exist_in_wave():
    with pytest.raises(ValueError, match="active subject is absent"):
        plan_wave_admission(
            wave(item("known")),
            budget=WaveExecutionBudget(
                max_parallel=1,
                max_per_identity=1,
                max_per_family=1,
            ),
            active_subjects=(("repository", "missing"),),
        )


def test_blocked_subject_reserves_collision_without_consuming_execution_budget():
    blocked = item(
        "blocked",
        priority="P0",
        lead="ONE",
        repository="owner/shared",
        family="family-a",
    )
    colliding = item(
        "colliding",
        priority="P0",
        lead="VOSS",
        repository="owner/shared",
        family="family-b",
    )
    unrelated = item(
        "unrelated",
        priority="P1",
        lead="ONE",
        repository="owner/unrelated",
        family="family-a",
    )

    planned = plan_wave_admission(
        wave(blocked, colliding, unrelated),
        budget=WaveExecutionBudget(
            max_parallel=1,
            max_per_identity=1,
            max_per_family=1,
            max_per_lane=1,
        ),
        blocked_subjects=(("repository", "blocked"),),
    )

    assert [selected.subject_id for selected in planned.selected] == [
        "unrelated"
    ]
    reasons = {
        deferred.subject_id: deferred.reason
        for deferred in planned.deferred
    }
    assert reasons["blocked"] == "BLOCKED_ACTIVE"
    assert reasons["colliding"] == "COLLISION"
    assert planned.blocked_subjects == (("repository", "blocked"),)
    assert planned.summary()["blocked_subjects"] == ["repository:blocked"]


def test_subject_cannot_be_active_and_blocked():
    with pytest.raises(ValueError, match="both active and blocked"):
        plan_wave_admission(
            wave(item("same")),
            budget=WaveExecutionBudget(
                max_parallel=1,
                max_per_identity=1,
                max_per_family=1,
            ),
            active_subjects=(("repository", "same"),),
            blocked_subjects=(("repository", "same"),),
        )
