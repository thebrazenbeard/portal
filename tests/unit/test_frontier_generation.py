from runner.frontier import derive_frontiers
from runner.models import DependencyReaction, ExactSubject, FrontierStatus
from runner.propagate import Invalidation


def _invalidation(reaction=DependencyReaction.REREVIEW, *, consumer="vera", dependency_id="dep-1"):
    return Invalidation(
        dependency_id=dependency_id,
        provider="vera-control-plane",
        consumer=consumer,
        changed_subject=ExactSubject(
            repository="thebrazenbeard/vera-control-plane",
            ref="main",
            commit="abc123",
            path="control/current.yaml",
        ),
        reaction=reaction,
    )


def test_invalidation_becomes_matching_frontier():
    frontiers = derive_frontiers(
        (_invalidation(),),
        capability_lookup={"vera": {"read", "analyze"}},
    )
    assert len(frontiers) == 1
    assert frontiers[0].work_type == "REREVIEW"
    assert frontiers[0].status is FrontierStatus.READY
    assert frontiers[0].required_capabilities == ("analyze",)


def test_missing_capability_yields_waiting_authority():
    frontiers = derive_frontiers(
        (_invalidation(),),
        capability_lookup={"vera": {"read"}},
    )
    assert frontiers[0].status is FrontierStatus.WAITING_AUTHORITY


def test_block_reaction_yields_waiting_dependency():
    frontiers = derive_frontiers(
        (_invalidation(DependencyReaction.BLOCK),),
        capability_lookup={"vera": {"read", "analyze"}},
    )
    assert frontiers[0].status is FrontierStatus.WAITING_DEPENDENCY


def test_no_action_creates_no_frontier():
    frontiers = derive_frontiers(
        (_invalidation(DependencyReaction.NO_ACTION),),
        capability_lookup={"vera": {"read", "analyze"}},
    )
    assert frontiers == ()


def test_shared_provider_evidence_does_not_create_cross_consumer_collision():
    frontiers = derive_frontiers(
        (
            _invalidation(consumer="vera", dependency_id="dep-vera"),
            _invalidation(consumer="project-runner", dependency_id="dep-runner"),
        ),
        capability_lookup={
            "vera": {"read", "analyze"},
            "project-runner": {"read", "analyze"},
        },
    )
    assert set(frontiers[0].collision_keys).isdisjoint(frontiers[1].collision_keys)


def test_scheduling_hold_is_distinct_from_missing_capability():
    frontiers = derive_frontiers(
        (_invalidation(),),
        capability_lookup={"vera": {"read", "analyze"}},
        scheduling_lookup={"vera": False},
    )
    assert frontiers[0].status is FrontierStatus.WAITING_SCHEDULING
    assert frontiers[0].priority_inputs["scheduling_eligible"] == 0
    assert frontiers[0].priority_inputs["authority_available"] == 1


def test_unknown_project_remains_authority_blocked_when_scheduling_lookup_is_supplied():
    frontiers = derive_frontiers(
        (_invalidation(),),
        capability_lookup={},
        scheduling_lookup={},
    )
    assert frontiers[0].status is FrontierStatus.WAITING_AUTHORITY
