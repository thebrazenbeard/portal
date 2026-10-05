import pytest

from runner.budgets import BudgetEnvelope
from runner.decompose import admit_child_work
from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _subject(commit="a" * 40):
    return ExactSubject(
        repository="thebrazenbeard/vera-control-plane",
        ref="main",
        commit=commit,
        path="control/current.yaml",
    )


def _work(id, *, parent=None, depth=0, operation="REREVIEW", subject=None, required=("analyze",)):
    return WorkUnit(
        id=id,
        root_frontier_id="frontier-root",
        parent_work_id=parent,
        inputs=(subject or _subject(),),
        operation=operation,
        required_capabilities=required,
        collision_keys=("project:vera",),
        recursion_depth=depth,
        budget_allocation={"children": 2, "active": 1, "retries": 1, "backend_jobs": 1},
        expected_outputs=("receipt",),
        completion_criteria=("verified",),
        status=WorkUnitStatus.PENDING,
    )


def _budget(depth=0):
    return BudgetEnvelope(
        lineage_id="run-1",
        max_depth=3,
        depth=depth,
        remaining_children=4,
        remaining_active=3,
        remaining_retries=2,
        remaining_backend_jobs=2,
    )


def _admit(parent, child, *, budget=None, ancestry=()):
    return admit_child_work(
        parent=parent,
        child=child,
        parent_budget=budget or _budget(parent.recursion_depth),
        parent_capabilities={"read", "analyze"},
        target_capabilities={"read", "analyze"},
        ancestry_fingerprints=ancestry,
        child_children=1,
        child_active=1,
        child_retries=1,
        child_backend_jobs=1,
    )


def test_self_cycle_is_rejected_even_with_new_work_id():
    parent = _work("a")
    child = _work("b", parent="a", depth=1)
    with pytest.raises(ValueError, match="cycle"):
        _admit(parent, child)


def test_ancestry_cycle_a_to_b_to_a_is_rejected():
    a = _work("a")
    b = _work("b", parent="a", depth=1, operation="RETEST", subject=_subject("b" * 40))
    admitted_b = _admit(a, b)
    a_again = _work("a2", parent="b", depth=2)

    with pytest.raises(ValueError, match="cycle"):
        _admit(
            admitted_b.work,
            a_again,
            budget=admitted_b.child_budget,
            ancestry=admitted_b.ancestry_fingerprints,
        )


def test_valid_distinct_child_is_admitted_with_narrowed_caps_and_lineage_budget():
    parent = _work("a")
    child = _work("b", parent="a", depth=1, operation="RETEST", subject=_subject("b" * 40))
    result = _admit(parent, child)

    assert result.effective_capabilities == ("analyze", "read")
    assert result.child_budget.lineage_id == "run-1"
    assert result.child_budget.scope_id == (
        "work:" + work_unit_fingerprint(result.work)
    )
    assert result.child_budget.depth == 1
    assert work_unit_fingerprint(result.work) in result.ancestry_fingerprints


def test_child_cannot_require_capability_outside_inherited_ceiling():
    parent = _work("a")
    child = _work(
        "b",
        parent="a",
        depth=1,
        operation="PRIVILEGED_EFFECT",
        subject=_subject("b" * 40),
        required=("privileged_effect",),
    )
    with pytest.raises(ValueError, match="capability"):
        _admit(parent, child)


def test_child_depth_must_follow_parent():
    parent = _work("a")
    child = _work("b", parent="a", depth=2, operation="RETEST", subject=_subject("b" * 40))
    with pytest.raises(ValueError, match="depth"):
        _admit(parent, child)


def test_changing_subject_oscillation_is_bounded_by_max_depth():
    root_budget = BudgetEnvelope(
        lineage_id="oscillation-lineage",
        max_depth=2,
        depth=0,
        remaining_children=2,
        remaining_active=2,
        remaining_retries=2,
        remaining_backend_jobs=2,
    )
    a = _work("a", subject=_subject("a" * 40))
    b = _work(
        "b",
        parent="a",
        depth=1,
        operation="RETEST",
        subject=_subject("b" * 40),
    )
    admitted_b = _admit(a, b, budget=root_budget)

    c = _work(
        "c",
        parent="b",
        depth=2,
        operation="REREVIEW",
        subject=_subject("c" * 40),
    )
    admitted_c = _admit(
        admitted_b.work,
        c,
        budget=admitted_b.child_budget,
        ancestry=admitted_b.ancestry_fingerprints,
    )

    d = _work(
        "d",
        parent="c",
        depth=3,
        operation="RETEST",
        subject=_subject("d" * 40),
    )
    with pytest.raises(ValueError, match="depth exhausted"):
        _admit(
            admitted_c.work,
            d,
            budget=admitted_c.child_budget,
            ancestry=admitted_c.ancestry_fingerprints,
        )
