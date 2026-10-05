from runner.collisions import partition_collision_groups
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus


def _frontier(id, collision_keys):
    return Frontier(
        id=id,
        project=id,
        subject=ExactSubject(repository=f"repo/{id}", ref="main", commit="abc"),
        work_type="RETEST",
        reason="test",
        dependencies=(f"dep-{id}",),
        required_capabilities=("analyze",),
        collision_keys=collision_keys,
        cost_class=CostClass.SMALL,
        priority_inputs={},
        status=FrontierStatus.READY,
    )


def test_shared_collision_key_groups_frontiers():
    a = _frontier("a", ("repo:x",))
    b = _frontier("b", ("repo:x", "path:y"))
    groups = partition_collision_groups((a, b))
    assert len(groups) == 1
    assert {item.id for item in groups[0]} == {"a", "b"}


def test_unrelated_frontiers_remain_independent():
    a = _frontier("a", ("repo:x",))
    b = _frontier("b", ("repo:y",))
    groups = partition_collision_groups((a, b))
    assert len(groups) == 2


def test_collision_grouping_is_transitive():
    a = _frontier("a", ("key:x",))
    b = _frontier("b", ("key:x", "key:y"))
    c = _frontier("c", ("key:y",))
    groups = partition_collision_groups((a, b, c))
    assert len(groups) == 1
    assert {item.id for item in groups[0]} == {"a", "b", "c"}
