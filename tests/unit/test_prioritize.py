from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus
from runner.prioritize import rank_frontiers


def _frontier(id, *, status=FrontierStatus.READY, priority_inputs=None):
    return Frontier(
        id=id,
        project=id,
        subject=ExactSubject(repository=f"repo/{id}", ref="main", commit="abc"),
        work_type="RETEST",
        reason="test",
        dependencies=(f"dep-{id}",),
        required_capabilities=("analyze",),
        collision_keys=(f"project:{id}",),
        cost_class=CostClass.SMALL,
        priority_inputs=priority_inputs or {},
        status=status,
    )


def test_ready_work_ranks_ahead_of_authority_blocked_peer():
    ready = _frontier("ready", priority_inputs={"declared_priority": 5})
    blocked = _frontier(
        "blocked",
        status=FrontierStatus.WAITING_AUTHORITY,
        priority_inputs={"declared_priority": 100},
    )
    ranked = rank_frontiers((blocked, ready))
    assert ranked[0].frontier.id == "ready"
    assert ranked[1].frontier.id == "blocked"


def test_priority_exposes_nonzero_factor_reasons():
    ranked = rank_frontiers((_frontier("a", priority_inputs={"fanout": 3}),))
    assert any("fanout=3" in reason for reason in ranked[0].reasons)


def test_tie_breaking_is_stable_across_input_order():
    a = _frontier("a")
    b = _frontier("b")
    forward = [item.frontier.id for item in rank_frontiers((a, b))]
    reverse = [item.frontier.id for item in rank_frontiers((b, a))]
    assert forward == reverse
