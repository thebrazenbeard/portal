import pytest

from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _subject(commit="a" * 40, path="control/current.yaml"):
    return ExactSubject(
        repository="thebrazenbeard/vera-control-plane",
        ref="main",
        commit=commit,
        path=path,
    )


def _work(
    *,
    id="work-1",
    inputs=None,
    operation="REREVIEW",
    required_capabilities=("analyze", "read"),
    collision_keys=("project:vera", "ref:vera:main"),
    recursion_depth=1,
    budget_allocation=None,
    expected_outputs=("review-receipt", "status"),
    completion_criteria=("exact-subject-current", "independent-verification"),
):
    return WorkUnit(
        id=id,
        root_frontier_id="frontier-root",
        parent_work_id="work-parent",
        inputs=tuple(inputs or (_subject(),)),
        operation=operation,
        required_capabilities=required_capabilities,
        collision_keys=collision_keys,
        recursion_depth=recursion_depth,
        budget_allocation=budget_allocation or {"children": 2, "retries": 1},
        expected_outputs=expected_outputs,
        completion_criteria=completion_criteria,
        status=WorkUnitStatus.PENDING,
    )


def test_work_fingerprint_ignores_ids_and_incidental_order():
    a = _work()
    b = _work(
        id="different-id",
        inputs=tuple(reversed(a.inputs)),
        required_capabilities=tuple(reversed(a.required_capabilities)),
        collision_keys=tuple(reversed(a.collision_keys)),
        expected_outputs=tuple(reversed(a.expected_outputs)),
        completion_criteria=tuple(reversed(a.completion_criteria)),
    )
    assert work_unit_fingerprint(a) == work_unit_fingerprint(b)


def test_work_fingerprint_changes_with_exact_subject_or_operation():
    baseline = _work()
    moved = _work(inputs=(_subject(commit="b" * 40),))
    different_operation = _work(operation="RETEST")

    assert work_unit_fingerprint(baseline) != work_unit_fingerprint(moved)
    assert work_unit_fingerprint(baseline) != work_unit_fingerprint(different_operation)


def test_negative_recursion_depth_is_rejected():
    with pytest.raises(ValueError, match="recursion depth"):
        _work(recursion_depth=-1)


def test_negative_budget_allocation_is_rejected():
    with pytest.raises(ValueError, match="budget allocation"):
        _work(budget_allocation={"children": -1})
