from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _work(payload):
    return WorkUnit(
        id="work-1",
        root_frontier_id="frontier-1",
        parent_work_id=None,
        inputs=(ExactSubject(repository="thebrazenbeard/project-runner", ref="main", commit="a" * 40),),
        operation="GITHUB",
        required_capabilities=("read",),
        collision_keys=("repo:project-runner",),
        recursion_depth=0,
        budget_allocation={"active": 1},
        expected_outputs=("github-result",),
        completion_criteria=("readback",),
        status=WorkUnitStatus.PENDING,
        payload=payload,
    )


def test_work_payload_is_part_of_semantic_identity_but_key_order_is_not():
    a = _work({"github": {"operation": "READ_REF", "repository": "thebrazenbeard/project-runner", "ref": "main"}})
    b = _work({"github": {"ref": "main", "repository": "thebrazenbeard/project-runner", "operation": "READ_REF"}})
    c = _work({"github": {"operation": "READ_REF", "repository": "thebrazenbeard/project-runner", "ref": "other"}})

    assert work_unit_fingerprint(a) == work_unit_fingerprint(b)
    assert work_unit_fingerprint(a) != work_unit_fingerprint(c)
