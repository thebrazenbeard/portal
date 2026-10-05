from runner.dedup import deduplicate_frontiers, frontier_fingerprint
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus


def _frontier(
    *,
    id="f-1",
    dependencies=("dep-b", "dep-a"),
    collision_keys=("project:vera", "path:x"),
    status=FrontierStatus.READY,
):
    return Frontier(
        id=id,
        project="vera",
        subject=ExactSubject(
            repository="thebrazenbeard/vera-control-plane",
            ref="main",
            commit="abc123",
            path="control/current.yaml",
        ),
        work_type="REREVIEW",
        reason="provider moved",
        dependencies=dependencies,
        required_capabilities=("analyze",),
        collision_keys=collision_keys,
        cost_class=CostClass.SMALL,
        priority_inputs={"fanout": 2},
        status=status,
    )


def test_fingerprint_ignores_incidental_collection_order():
    a = _frontier()
    b = _frontier(
        id="different-id",
        dependencies=("dep-a", "dep-b"),
        collision_keys=("path:x", "project:vera"),
    )
    assert frontier_fingerprint(a) == frontier_fingerprint(b)


def test_equivalent_frontiers_deduplicate_even_with_different_ids():
    assert len(deduplicate_frontiers((_frontier(), _frontier(id="f-2")))) == 1


def test_same_work_from_different_dependency_paths_deduplicates_and_merges_provenance():
    a = _frontier(id="a", dependencies=("dep-a",))
    b = _frontier(id="b", dependencies=("dep-b",))
    assert frontier_fingerprint(a) == frontier_fingerprint(b)
    result = deduplicate_frontiers((a, b))
    assert len(result) == 1
    assert result[0].dependencies == ("dep-a", "dep-b")


def test_dedup_does_not_erase_blocking_state():
    ready = _frontier(id="ready", status=FrontierStatus.READY)
    blocked = _frontier(id="blocked", status=FrontierStatus.WAITING_AUTHORITY)
    result = deduplicate_frontiers((ready, blocked))
    assert result[0].status is FrontierStatus.WAITING_AUTHORITY
