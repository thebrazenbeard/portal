import pytest

from runner.budgets import BudgetEnvelope, allocate_child_budget


def _budget(**overrides):
    values = {
        "lineage_id": "run-1",
        "max_depth": 3,
        "depth": 0,
        "remaining_children": 6,
        "remaining_active": 4,
        "remaining_retries": 3,
        "remaining_backend_jobs": 5,
    }
    values.update(overrides)
    return BudgetEnvelope(**values)


def test_child_allocation_subtracts_from_parent_and_preserves_lineage():
    parent = _budget()
    parent_after, child = allocate_child_budget(
        parent,
        child_scope_id="child-a",
        child_children=2,
        child_active=1,
        child_retries=1,
        child_backend_jobs=2,
    )

    assert parent_after.remaining_children == 4
    assert parent_after.remaining_active == 3
    assert parent_after.remaining_retries == 2
    assert parent_after.remaining_backend_jobs == 3
    assert child.lineage_id == parent.lineage_id
    assert child.scope_id == "child-a"
    assert child.depth == 1
    assert child.remaining_children == 2
    assert child.remaining_active == 1
    assert child.remaining_retries == 1
    assert child.remaining_backend_jobs == 2


def test_recursive_child_cannot_reset_budget():
    _, child = allocate_child_budget(
        _budget(remaining_children=2),
        child_scope_id="child-a",
        child_children=2,
        child_active=1,
        child_retries=1,
        child_backend_jobs=1,
    )
    with pytest.raises(ValueError, match="children"):
        allocate_child_budget(
            child,
            child_scope_id="grandchild-a",
            child_children=3,
            child_active=0,
            child_retries=0,
            child_backend_jobs=0,
        )


def test_over_allocation_fails_closed():
    with pytest.raises(ValueError, match="backend jobs"):
        allocate_child_budget(
            _budget(remaining_backend_jobs=1),
            child_scope_id="child-a",
            child_children=0,
            child_active=0,
            child_retries=0,
            child_backend_jobs=2,
        )


def test_depth_exhaustion_terminates_decomposition():
    exhausted = _budget(max_depth=1, depth=1)
    with pytest.raises(ValueError, match="depth exhausted"):
        allocate_child_budget(
            exhausted,
            child_scope_id="child-a",
            child_children=0,
            child_active=0,
            child_retries=0,
            child_backend_jobs=0,
        )


def test_negative_budget_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        _budget(remaining_retries=-1)


def test_child_scope_must_be_distinct_and_nonempty():
    with pytest.raises(ValueError, match="required"):
        allocate_child_budget(
            _budget(),
            child_scope_id="",
            child_children=0,
            child_active=0,
            child_retries=0,
            child_backend_jobs=0,
        )
    with pytest.raises(ValueError, match="differ"):
        allocate_child_budget(
            _budget(),
            child_scope_id="root",
            child_children=0,
            child_active=0,
            child_retries=0,
            child_backend_jobs=0,
        )
